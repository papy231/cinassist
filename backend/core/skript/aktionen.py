"""Skript-gesteuerte Bildprüfung — „zeigt der Take die Aktion, die das Skript an dieser Stelle verlangt?“

Warum so (Messung 2026-08-19, llava:7b, 448/896 px): Ja/Nein-Fragen zu **Handlungen von Personen** („Is someone playing
a guitar?“, „sitting on a sofa?“, „standing?“) werden zuverlässig beantwortet; Fragen zu **kleinen Requisiten** (Mülleimer,
Stehlampe, Fernseher im Hintergrund) oft falsch verneint. Deshalb:
  1. Fragen je Skript-Aktion werden vom LLM so formuliert, dass sie Personen/Posen/große Objekte betreffen (1–3 je Aktion).
  2. Primärsignal = VQA-Ja auf dichten Frames (alle 5 s stumme Takes, alle 10 s Dialog-Takes).
  3. Sekundärsignal = CLIP-Ähnlichkeit Frame ↔ Aktionstext (zero-shot, relativ innerhalb des Takes) → „wahrscheinlich“.
  4. Ergebnis je Take: Zeitfenster je Skript-Aktion + Anzahl Ja-Frames; je Szene: Coverage (gedreht / unsicher / fehlt).
Alles gecacht (PROXY_DIR/vqa/<clip>.json), damit Re-Runs billig sind.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

from backend.core.config import FFMPEG_BIN, PROXY_DIR
from backend.core.database import Clip, SkriptSzene, TakeKontext
from backend.core.medien import clip_stem
from backend.core.skript.llm import frage_json
from backend.core.vision_describe import OLLAMA_URL, vision_modell

logger = logging.getLogger("cinassist.skript.aktionen")
VQA_DIR = PROXY_DIR / "vqa"
VQA_DIR.mkdir(parents=True, exist_ok=True)
SCHRITT_STUMM = 5.0
SCHRITT_DIALOG = 10.0
BREITE = 448


# ─── Fragen je Szene (LLM, gecacht im Skript-Szenen-JSON) ───────────────────────

def fragen_fuer_szene(db, sz: SkriptSzene, neu: bool = False) -> list[dict]:
    # Kopie! Das Original-Dict in place zu ändern lässt SQLAlchemy keine Änderung erkennen (Vergleich gegen den
    # ebenfalls mutierten „committed“-Wert) → Fragen wurden nie gespeichert.
    zj = dict(sz.zusammenfassung_json) if isinstance(sz.zusammenfassung_json, dict) else {}
    if not neu and isinstance(zj.get("vqa"), list) and zj["vqa"]:
        return zj["vqa"]
    aktionen = [z for z in sz.zeilen if z.art == "aktion"]
    if not aktionen:
        return []
    block = "\n".join(f"[A{z.nr}] {z.text}" for z in aktionen)
    prompt = f"""Du hilfst, Filmmaterial gegen ein Drehbuch zu prüfen. Ein Bildmodell kann nur KONKRETE, SICHTBARE Körperhandlungen/Posen und große Objekte erkennen — keine Stimmungen, Schatten, Absichten, Blickrichtungen, kleinen Requisiten.

Für JEDE Aktionszeile unten (alle Nummern A0…, keine auslassen) gib GENAU EINEN Eintrag mit 2–3 englischen Ja/Nein-Fragen. Jede Frage beginnt mit "Is someone", "Are two people", "Is a person" oder "Is there a large …" und nennt eine Tätigkeit/Pose (sitting on a sofa, lying on a sofa, playing a guitar, writing on paper, standing at a door, kneeling on the floor, hugging another person, walking through a room, holding a cup, touching a TV screen, lying motionless). Mehrere Handlungen in einer Zeile → mehrere Fragen (max. 3, die wichtigsten). Adaption bedenken: "writing a song"/"music"/"band" kann im Dreh "playing a guitar" sein → bei Song/Musik IMMER zusätzlich "Is someone playing a guitar?" fragen. Dazu ein englisches Label (2–5 Wörter) je Aktion.
VERBOTEN: "shadow", "looking", "searching", "feeling", "seems", "dark", "empty area", kleine Gegenstände (bin, lamp, bag, cup als Hauptfrage).

Antworte NUR als JSON: {{"fragen": [{{"aktion_nr": 0, "label": "person playing guitar on sofa", "fragen": ["Is someone sitting on a sofa?", "Is someone playing a guitar?", "Is someone writing on paper?"]}}]}}

Szene {sz.nummer}: {sz.ueberschrift}
{block}"""
    out = frage_json(prompt, num_predict=900)
    fragen = out.get("fragen") if isinstance(out, dict) else None
    if not isinstance(fragen, list):
        return []
    import re as _re
    verboten = _re.compile(r"\b(shadow|shadows|looking|look|searching|feeling|seems|seem|dark|empty area|obscured|visible in the background|expression|emotion|mood|sad|happy|worried)\b", _re.I)
    sauber = []
    for f in fragen:
        try:
            nr = int(f.get("aktion_nr"))
        except Exception:  # noqa: BLE001
            continue
        qs = [str(q).strip() for q in (f.get("fragen") or []) if str(q).strip() and not verboten.search(str(q))][:3]
        # Skript-Text mit Musik/Song → Gitarre immer mitfragen (Adaption)
        txt = next((z.text for z in aktionen if z.nr == nr), "").lower()
        if any(w in txt for w in ("song", "music", "guitar", "lied", "musik")) and not any("guitar" in q.lower() for q in qs):
            qs = (qs + ["Is someone playing a guitar?"])[:3]
        if qs:
            sauber.append({"aktion_nr": nr, "label": str(f.get("label") or "")[:60], "fragen": qs})
    zj["vqa"] = sauber
    sz.zusammenfassung_json = zj
    try:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(sz, "zusammenfassung_json")
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return sauber


# ─── VQA ──────────────────────────────────────────────────────────────────────────

def _ja_nein(img_b64: str, frage: str, modell: str) -> bool | None:
    body = json.dumps({"model": modell, "prompt": f"Answer with yes or no only. {frage}", "images": [img_b64], "stream": False,
                       "keep_alive": "3m", "options": {"temperature": 0, "num_predict": 3}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}), timeout=60) as r:
            a = (json.loads(r.read()).get("response") or "").strip().lower()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"VQA fehlgeschlagen: {e}")
        return None
    if a.startswith("yes"):
        return True
    if a.startswith("no"):
        return False
    return None


def _frames(proxy: Path, schritt: float, dauer: float) -> list[tuple[float, Path]]:
    out = []
    t = schritt / 2
    tmp = VQA_DIR / "frames"
    tmp.mkdir(exist_ok=True)
    while t < dauer:
        fp = tmp / f"{proxy.stem}_{int(t * 10):06d}.jpg"
        if not fp.exists():
            subprocess.run([FFMPEG_BIN, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(proxy), "-frames:v", "1", "-q:v", "3",
                            "-vf", f"scale={BREITE}:-2", str(fp)], capture_output=True, timeout=60)
        if fp.exists() and fp.stat().st_size > 0:
            out.append((round(t, 2), fp))
        t += schritt
    return out


def _lade_cache(clip: Clip) -> dict:
    cache_p = VQA_DIR / f"{clip.id}.json"
    try:
        return json.loads(cache_p.read_text("utf-8")) if cache_p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def pruefe_take(db, tk: TakeKontext, clip: Clip, fragen: list[dict], fortschritt=None) -> dict:
    """Phase 1: alle Fragen der Szene an dichte Frames des Takes stellen (Cache je (Zeit, Frage) in PROXY_DIR/vqa/<clip>.json)
    + CLIP-Zweitsignal. Die Bewertung (Spans) macht `bewerte_take` — mit szenenweiten Gewichten."""
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not (proxy.exists() and proxy.stat().st_size > 0) or not fragen:
        return {}
    modell = vision_modell() or "llava:7b"
    cache_p = VQA_DIR / f"{clip.id}.json"
    cache = _lade_cache(clip)
    hat_dialog = any(i.get("art") == "spiel" and i.get("skript_zeile_id") for i in (tk.zeilen or []))
    schritt = SCHRITT_DIALOG if hat_dialog else SCHRITT_STUMM
    frames = _frames(proxy, schritt, float(clip.dauer or 0.0))
    try:
        from backend.core import clip_encoder as CE
        import numpy as np
        labels = {f["aktion_nr"]: f.get("label") for f in fragen if f.get("label")}
        t_emb = {nr: CE.embed_text(lbl) for nr, lbl in labels.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"CLIP-Zweitsignal nicht verfügbar: {e}")
        CE = None; t_emb = {}
    gesamt = len(frames) * sum(len(f["fragen"]) for f in fragen)
    done = 0
    for t, fp in frames:
        b64 = None
        if CE is not None and t_emb:
            try:
                fe = CE.embed_image(fp)
                for nr, te in t_emb.items():
                    cache[f"clip|{t:.1f}|{nr}"] = float(np.dot(fe, te))
            except Exception:  # noqa: BLE001
                pass
        for f in fragen:
            for q in f["fragen"]:
                key = f"{t:.1f}|{q}"
                if key not in cache:
                    if b64 is None:
                        b64 = base64.b64encode(fp.read_bytes()).decode()
                    cache[key] = _ja_nein(b64, q, modell)
                done += 1
        if fortschritt and gesamt:
            fortschritt(done / gesamt)
        try:
            cache_p.write_text(json.dumps(cache), "utf-8")
        except Exception:  # noqa: BLE001
            pass
    cache["_meta"] = {"schritt": schritt, "frames": [t for t, _ in frames]}
    cache_p.write_text(json.dumps(cache), "utf-8")
    return cache


def frage_gewichte(clips: list[Clip], fragen: list[dict]) -> dict[str, float]:
    """Szenenweite Gewichte je Frage: Anteil „Ja“ über alle geprüften Frames aller Takes der Szene. Fragen, die fast
    überall wahr sind („sitting on a sofa“ in einer Sofa-Szene), diskriminieren nichts → Gewicht sinkt.
    Gewicht = 1 − ja_rate (mind. 0,1); zusätzlich halb, wenn die Frage mehreren Aktionen dient."""
    zaehler: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in clips:
        cache = _lade_cache(c)
        for k, v in cache.items():
            if k.startswith("clip|") or k == "_meta" or "|" not in k:
                continue
            q = k.split("|", 1)[1]
            zaehler[q][1] += 1
            if v is True:
                zaehler[q][0] += 1
    haeufigkeit: dict[str, int] = defaultdict(int)
    for f in fragen:
        for q in set(f["fragen"]):
            haeufigkeit[q] += 1
    gew: dict[str, float] = {}
    for f in fragen:
        for q in f["fragen"]:
            ja, n = zaehler.get(q, [0, 0])
            rate = ja / n if n else 0.0
            g = max(0.1, 1.0 - rate)
            if haeufigkeit.get(q, 1) > 1:
                g *= 0.5
            gew[q] = round(g, 3)
    return gew


def bewerte_take(db, tk: TakeKontext, clip: Clip, fragen: list[dict], gewichte: dict[str, float]) -> dict:
    """Phase 2: aus dem Cache Spans je Aktion — ein Frame zeigt die Aktion, wenn die gewichtete Ja-Summe ≥ 0,6
    (eine trennscharfe, seltene Frage genügt; zwei generische nicht)."""
    cache = _lade_cache(clip)
    meta = cache.get("_meta") or {}
    frames = meta.get("frames") or []
    schritt = float(meta.get("schritt") or SCHRITT_STUMM)
    if not frames:
        return {}
    ergebnis: dict = {}
    for f in fragen:
        nr = f["aktion_nr"]
        ts = []
        for t in frames:
            score = sum(gewichte.get(q, 0.5) for q in f["fragen"] if cache.get(f"{t:.1f}|{q}") is True)
            if score >= 0.6:
                ts.append(float(t))
        spans: list[list[float]] = []
        for t in sorted(ts):
            if spans and t - spans[-1][1] <= schritt * 1.5:
                spans[-1][1] = t + schritt / 2
            else:
                spans.append([max(0.0, t - schritt / 2), t + schritt / 2])
        sims = [(float(t), cache.get(f"clip|{t:.1f}|{nr}")) for t in frames if cache.get(f"clip|{t:.1f}|{nr}") is not None]
        if sims:
            vals = sorted(v for _, v in sims); med = vals[len(vals) // 2]
            best_t, best_v = max(sims, key=lambda x: x[1])
            sim_rel, sim_t = round(float(best_v - med), 4), best_t
        else:
            sim_rel, sim_t = None, None
        ergebnis[str(nr)] = {"spans": [[round(a, 1), round(b, 1)] for a, b in spans], "ja": len(ts), "frames": len(frames),
                             "schritt": schritt, "clip_sim_rel": sim_rel, "clip_sim_t": sim_t, "label": f.get("label"),
                             "gewichte": {q: gewichte.get(q) for q in f["fragen"]}}
    tk.aktionen = ergebnis
    db.commit()
    return ergebnis


def aktions_coverage(sz: SkriptSzene, tks: list[TakeKontext], clips: dict) -> dict:
    """Je Skript-Aktion: gedreht (VQA-Spans in ≥ 1 Take) / unsicher (nur CLIP-Auffälligkeit) / fehlt."""
    out: dict = {}
    for z in sz.zeilen:
        if z.art != "aktion":
            continue
        takes_ja, takes_unsicher = [], []
        for tk in tks:
            a = (tk.aktionen or {}).get(str(z.nr))
            if not a:
                continue
            c = clips.get(tk.clip_id)
            eintrag = {"clip_id": str(tk.clip_id), "dateiname": c.dateiname if c else None, "einstellung": tk.einstellung,
                       "spans": a.get("spans"), "ja": a.get("ja"), "frames": a.get("frames"), "clip_sim_rel": a.get("clip_sim_rel"), "clip_sim_t": a.get("clip_sim_t")}
            if a.get("spans"):
                takes_ja.append(eintrag)
            elif (a.get("clip_sim_rel") or 0) >= 0.03:
                takes_unsicher.append(eintrag)
        status = "gedreht" if takes_ja else ("unsicher" if takes_unsicher else "fehlt")
        out[str(z.nr)] = {"text": z.text, "status": status, "takes": takes_ja or takes_unsicher}
    return out
