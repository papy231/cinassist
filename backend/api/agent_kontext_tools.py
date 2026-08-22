"""Agent-Tools der Kontext-Schicht — verbindet den KI-Agenten mit dem, was das System wirklich weiß und kann.

Wissen (lesen):
  get_script_overview     Drehbuch, Szenen, Figuren (Skript↔Film), Abdeckung, aktueller Schnittplan
  get_scene_context       eine Szene: Beats, Takes (wer deckt welchen Beat, mit welcher Evidenz), Bildprüfung, Lücken
  get_take_details        ein Take: Klappe, Spielfenster, Transkript (aligniert), Aktionen, Gesichter, Beat-Spans
  get_plan                der Schnittplan: jedes Segment mit Grund + Belegen (der Agent kann jede Coupe ERKLÄREN)
  search_transcripts      einen Satz in allen Take-Transkripten finden

Handeln (jede Änderung wird als PROPOSAL mit Geister-Vorschau in den Editor gepusht — nie direkt angewandt):
  edit_timeline           generische Timeline-Kommandos (split/trim/deleteRange/move/insert/fades/…), server-validiert
  regenerate_schnittplan  echten Beat-Planer laufen lassen (L5) → neuer Plan + Timeline-Vorschlag
  swap_beat_source        die Quelle EINES Beats austauschen (Beat-Matrix) → Timeline-Vorschlag

Die Validierung der Timeline-Kommandos (`pruefe_timeline_kommandos`) ist pur und einzeln getestet
(tests/test_agent_kommandos.py).
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import uuid as uuid_mod
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import (
    Clip, GesichtsCluster, Schnittplan, Skript, SkriptSzene, SkriptZeile, SzenenKontext, TakeKontext, Timeline,
)

logger = logging.getLogger("cinassist.agent.kontext")

# Timeline-Snapshot des laufenden Agent-Laufs (vom Frontend geschickt) — für die Kommando-Validierung.
AKTUELLER_TL_STATE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("agent_tl_state", default=None)


# ─────────────────────────── gemeinsame Helfer ───────────────────────────

async def _aktives_skript(db: AsyncSession) -> Skript | None:
    r = await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()))
    return r.scalars().first()


async def _szenen(db: AsyncSession, skript_id) -> list[SkriptSzene]:
    r = await db.execute(select(SkriptSzene).where(SkriptSzene.skript_id == skript_id).order_by(SkriptSzene.reihenfolge))
    return list(r.scalars().all())


async def _letzter_plan(db: AsyncSession, plan_id: str | None = None) -> Schnittplan | None:
    """plan_id = gültige UUID → genau dieser Plan; alles andere (leer, „aktuell“, Halluzination) → neuester Plan.
    (Beobachtet im nativen Tool-Calling: das Modell erfindet plan_id-Werte — das darf nie zu „kein Plan“ führen.)"""
    q = select(Schnittplan)
    if plan_id:
        try:
            pid = uuid_mod.UUID(str(plan_id))
            return (await db.execute(q.where(Schnittplan.id == pid))).scalars().first()
        except ValueError:
            logger.info("get_plan: ungültige plan_id %r — nehme den neuesten Plan.", plan_id)
    return (await db.execute(q.order_by(Schnittplan.erstellt_am.desc()))).scalars().first()


async def _figuren_mapping(db: AsyncSession, skript_id) -> list[dict]:
    r = await db.execute(select(GesichtsCluster).where(GesichtsCluster.skript_id == skript_id))
    out = []
    for g in r.scalars().all():
        if g.name_skript:
            out.append({"skript": g.name_skript, "film": g.name_film, "takes": g.takes})
    return out


_TAKE_MUSTER = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[·/,]?\s*T(?:ake)?\s*(\d+)\s*$", re.IGNORECASE)


async def _finde_take(db: AsyncSession, ident: str) -> tuple[TakeKontext | None, Clip | None]:
    """Auflösung: Clip-UUID · Dateiname(-Fragment) · „2.1 T4“."""
    ident = (ident or "").strip()
    m = _TAKE_MUSTER.match(ident)
    if m:
        einst, take = m.group(1), int(m.group(2))
        r = await db.execute(select(TakeKontext).where(TakeKontext.einstellung == einst, TakeKontext.slate_take == take))
        tk = r.scalars().first()
        if tk:
            c = (await db.execute(select(Clip).where(Clip.id == tk.clip_id))).scalars().first()
            return tk, c
    try:
        cid = uuid_mod.UUID(ident)
        c = (await db.execute(select(Clip).where(Clip.id == cid))).scalars().first()
    except ValueError:
        stamm = ident.rsplit(".", 1)[0]
        c = (await db.execute(select(Clip).where(Clip.dateiname.ilike(f"%{stamm}%")))).scalars().first()
    if c is None:
        return None, None
    tk = (await db.execute(select(TakeKontext).where(TakeKontext.clip_id == c.id))).scalars().first()
    return tk, c


def _beat_titel(b: dict) -> str:
    if b.get("dialog_nr") is not None:
        t = (b.get("text_de") or b.get("text") or "").strip()
        return f"B{b['nr']} Z{b['dialog_nr']} {b.get('figur') or ''}: „{t[:60]}“"
    return f"B{b['nr']} ({b.get('art')}): {(b.get('text') or '')[:60]}"


# ─────────────────────────── Wissen ───────────────────────────

async def tool_get_script_overview(args: dict, db: AsyncSession) -> dict:
    sk = await _aktives_skript(db)
    if sk is None:
        return {"error": "Kein aktives Drehbuch importiert."}
    szenen = await _szenen(db, sk.id)
    ctxs = {c.skript_szene_id: c for c in (await db.execute(select(SzenenKontext))).scalars().all()}
    tks = (await db.execute(select(TakeKontext))).scalars().all()
    tks_je_szene: dict = {}
    for t in tks:
        if t.skript_szene_id is not None:
            tks_je_szene.setdefault(t.skript_szene_id, []).append(t)
    plan = await _letzter_plan(db)
    sz_rows = []
    for sz in szenen:
        ctx = ctxs.get(sz.id)
        beats = (ctx.takt if ctx else None) or []
        zeilen = (await db.execute(select(SkriptZeile).where(SkriptZeile.szene_id == sz.id))).scalars().all()
        sz_rows.append({
            "nummer": sz.nummer, "ueberschrift": sz.ueberschrift,
            "dialogzeilen": sum(1 for z in zeilen if z.art == "dialog"),
            "beats": len(beats),
            "takes": len(tks_je_szene.get(sz.id, [])),
            "aktions_coverage": {k: v.get("status") for k, v in ((ctx.aktions_coverage if ctx else None) or {}).items()},
        })
    plan_info = None
    if plan is not None:
        st = plan.statistik or {}
        plan_info = {"plan_id": str(plan.id), "name": plan.name, "modus": (plan.parameter or {}).get("modus"),
                     "eintraege": st.get("eintraege"), "dauer_s": st.get("dauer_s"), "luecken": len(st.get("luecken") or [])}
    return {"titel": sk.titel or sk.name, "skript_sprache": sk.sprache, "dreh_sprache": sk.ziel_sprache,
            "szenen": sz_rows, "figuren": await _figuren_mapping(db, sk.id), "aktueller_plan": plan_info}


async def tool_get_scene_context(args: dict, db: AsyncSession) -> dict:
    nummer = str(args.get("szene") or "").strip()
    sk = await _aktives_skript(db)
    if sk is None:
        return {"error": "Kein aktives Drehbuch."}
    sz = next((s for s in await _szenen(db, sk.id) if s.nummer == nummer), None)
    if sz is None:
        return {"error": f"Szene '{nummer}' nicht gefunden."}
    ctx = (await db.execute(select(SzenenKontext).where(SzenenKontext.skript_szene_id == sz.id))).scalars().first()
    beats = (ctx.takt if ctx else None) or []
    tks = (await db.execute(select(TakeKontext).where(TakeKontext.skript_szene_id == sz.id))).scalars().all()
    clips = {c.id: c for c in (await db.execute(select(Clip))).scalars().all()}
    take_rows = []
    for t in sorted(tks, key=lambda t: ((t.einstellung or ""), (t.slate_take or 0))):
        c = clips.get(t.clip_id)
        spans = []
        for sp in (t.takt or []):
            if not sp.get("evidenz"):
                continue
            ev = "A" if sp.get("anker") else ("s" if float(sp.get("sem") or 0) > 0 else "") + ("V" if sp.get("vqa") else "") + ("E" if sp.get("eroeffnung") else "")
            spans.append(f"B{sp['beat']} {sp['start']:.0f}–{sp['end']:.0f}s[{ev or '·'}]")
        take_rows.append({"clip": c.dateiname if c else str(t.clip_id), "clip_id": str(t.clip_id),
                          "einstellung": t.einstellung, "take": t.slate_take,
                          "abdeckung": t.abdeckung, "ng": (t.ng or {}).get("gruende"),
                          "beats_belegt": spans})
    plan = await _letzter_plan(db)
    plan_segs, luecken = [], []
    if plan is not None:
        for e in (plan.eintraege or []):
            if e.get("szene") == nummer:
                plan_segs.append({"nr": e["nr"], "einstellung": e.get("einstellung"), "take": e.get("take"),
                                  "in_s": e["in_s"], "out_s": e["out_s"], "art": e.get("art"), "beats": e.get("beats"),
                                  "grund": e.get("grund")})
        luecken = [l for l in ((plan.statistik or {}).get("luecken") or []) if str(l.get("szene")) == nummer]
    return {"szene": nummer, "ueberschrift": sz.ueberschrift,
            "beats": [_beat_titel(b) for b in beats],
            "takes": take_rows, "plan_segmente": plan_segs, "plan_luecken": luecken,
            "legende": "beats_belegt: A=alignierte Skriptzeile, s=Improvisation semantisch, V=Bild bestätigt, E=Eröffnung"}


async def tool_get_take_details(args: dict, db: AsyncSession) -> dict:
    tk, c = await _finde_take(db, str(args.get("take") or args.get("clip") or ""))
    if c is None:
        return {"error": f"Take/Clip '{args.get('take') or args.get('clip')}' nicht gefunden. Format: Dateiname, UUID oder „2.1 T4“."}
    if tk is None:
        return {"clip": c.dateiname, "clip_id": str(c.id), "hinweis": "Kein Take-Kontext (Kontext-Schicht nicht gelaufen)."}
    zeilen = []
    for it in (tk.zeilen or [])[:60]:
        zeilen.append({"t": round(float(it.get("start") or 0), 1), "art": it.get("art"),
                       "zeile": it.get("skript_zeile_nr"), "score": it.get("score"), "text": (it.get("text") or "")[:90]})
    cluster = {str(g.id): (g.name_film or g.name_skript) for g in (await db.execute(select(GesichtsCluster))).scalars().all()}
    gesichter = {cluster.get(k, k[:8]): {"anteil": v.get("anteil"), "spans": v.get("spans")}
                 for k, v in (tk.gesichter or {}).items()}
    return {"clip": c.dateiname, "clip_id": str(c.id), "dauer_s": c.dauer,
            "einstellung": tk.einstellung, "take": tk.slate_take, "slate_quelle": tk.slate_quelle,
            "spiel": [tk.spiel_start_s, tk.spiel_ende_s], "abdeckung": tk.abdeckung, "ng": tk.ng,
            "beat_spans": tk.takt, "transkript": zeilen, "gesichter": gesichter,
            "aktionen_bestaetigt": {k: v.get("spans") for k, v in (tk.aktionen or {}).items() if v.get("spans")}}


async def tool_get_plan(args: dict, db: AsyncSession) -> dict:
    plan = await _letzter_plan(db, args.get("plan_id"))
    if plan is None:
        return {"error": "Kein Schnittplan vorhanden. Mit regenerate_schnittplan einen erzeugen."}
    st = plan.statistik or {}
    szene = str(args.get("szene") or "").strip() or None
    quelle = [e for e in (plan.eintraege or []) if szene is None or e.get("szene") == szene]
    eintraege = []
    for e in quelle[:60]:
        eintraege.append({"nr": e["nr"], "szene": e["szene"], "einstellung": e.get("einstellung"), "take": e.get("take"),
                          "clip": e.get("dateiname"), "tl_start": e.get("tl_start"), "in_s": e["in_s"], "out_s": e["out_s"],
                          "dauer": e.get("dauer"), "art": e.get("art"), "beats": e.get("beats"),
                          "video_only": e.get("video_only"), "audio_only": e.get("audio_only"),
                          "grund": e.get("grund"), "beleg": (e.get("beleg") or [])[:3]})
    return {"plan_id": str(plan.id), "name": plan.name, "modus": (plan.parameter or {}).get("modus"),
            "erstellt": plan.erstellt_am.isoformat() if plan.erstellt_am else None,
            "dauer_s": st.get("dauer_s"), "eintraege": eintraege,
            "luecken": [l for l in (st.get("luecken") or []) if szene is None or str(l.get("szene")) == szene],
            "hinweis": "grund + beleg erklären jede Schnitt-Entscheidung; beats = Szenen-Takt-Nummern."}


async def tool_search_transcripts(args: dict, db: AsyncSession) -> dict:
    query = str(args.get("query") or "").strip()
    if len(query) < 3:
        return {"error": "query zu kurz (min. 3 Zeichen)."}
    szene = str(args.get("szene") or "").strip() or None
    from backend.core.skript.alignment import lexikalisch
    sk = await _aktives_skript(db)
    szenen = {s.id: s.nummer for s in (await _szenen(db, sk.id) if sk else [])}
    clips = {c.id: c.dateiname for c in (await db.execute(select(Clip))).scalars().all()}
    treffer = []
    for tk in (await db.execute(select(TakeKontext))).scalars().all():
        sz_nr = szenen.get(tk.skript_szene_id)
        if szene and sz_nr != szene:
            continue
        for it in tk.zeilen or []:
            text = str(it.get("text") or "")
            if not text:
                continue
            score = 1.0 if query.lower() in text.lower() else lexikalisch(query, text)
            if score >= 0.55:
                treffer.append({"score": round(score, 2), "szene": sz_nr, "clip": clips.get(tk.clip_id),
                                "einstellung": tk.einstellung, "take": tk.slate_take,
                                "t": round(float(it.get("start") or 0), 1), "art": it.get("art"), "text": text[:100]})
    treffer.sort(key=lambda x: -x["score"])
    return {"query": query, "treffer": treffer[:20], "gesamt": len(treffer)}


# ─────────────────────────── Handeln ───────────────────────────

_BEKANNTE_TYPEN = {"split", "delete", "deleteRange", "move", "trim", "insert", "setFade", "setGain",
                   "addMarker", "setRange", "loadSequence"}


def pruefe_timeline_kommandos(cmds: Any, tl_state: dict | None) -> tuple[list[dict], list[str], list[str]]:
    """Pure Validierung/Normalisierung der Agent-Kommandos gegen den Timeline-Snapshot.
    Liefert (gültige_kommandos, warnungen, fehler). Nie werfen — kaputte Kommandos landen in `fehler`."""
    warnungen: list[str] = []
    fehler: list[str] = []
    if not isinstance(cmds, list) or not cmds:
        return [], [], ["`commands` muss eine nicht-leere Liste von Kommando-Objekten sein."]
    tl = tl_state or {}
    total = float(tl.get("totalDuration") or 0.0)
    bekannte_tl_ids = {c.get("tlId") for c in (tl.get("clips") or []) if isinstance(c, dict)}
    je_id = {c.get("tlId"): c for c in (tl.get("clips") or []) if isinstance(c, dict)}

    def _zeit(v, name, cmd_i) -> float | None:
        try:
            t = float(v)
        except (TypeError, ValueError):
            fehler.append(f"Kommando {cmd_i}: {name} ist keine Zahl ({v!r}).")
            return None
        if total > 0 and (t < -0.001 or t > total + 0.001):
            warnungen.append(f"Kommando {cmd_i}: {name}={t:.2f}s auf [0, {total:.2f}] begrenzt.")
            t = min(max(t, 0.0), total)
        return max(0.0, t)

    ok: list[dict] = []
    for i, cmd in enumerate(cmds):
        if not isinstance(cmd, dict) or cmd.get("type") not in _BEKANNTE_TYPEN:
            fehler.append(f"Kommando {i}: unbekannter Typ {cmd.get('type') if isinstance(cmd, dict) else cmd!r}. "
                          f"Erlaubt: {sorted(_BEKANNTE_TYPEN)}")
            continue
        typ = cmd["type"]
        c = dict(cmd)
        if typ == "split":
            at = _zeit(c.get("at"), "at", i)
            if at is None:
                continue
            c["at"] = at
            ids = [x for x in (c.get("clipTlIds") or []) if x in bekannte_tl_ids]
            if c.get("clipTlIds") and not ids:
                fehler.append(f"Kommando {i}: keine der clipTlIds existiert auf der Timeline.")
                continue
            if ids:
                c["clipTlIds"] = ids
        elif typ == "delete":
            ids = [x for x in (c.get("tlIds") or []) if x in bekannte_tl_ids]
            fremd = [x for x in (c.get("tlIds") or []) if x not in bekannte_tl_ids]
            if fremd:
                warnungen.append(f"Kommando {i}: unbekannte tlIds ignoriert: {fremd[:3]}")
            if not ids:
                fehler.append(f"Kommando {i}: delete ohne gültige tlIds.")
                continue
            c["tlIds"] = ids
            c["ripple"] = bool(c.get("ripple", True))
        elif typ == "deleteRange":
            von = _zeit(c.get("from"), "from", i); bis = _zeit(c.get("to"), "to", i)
            if von is None or bis is None or bis - von < 0.01:
                fehler.append(f"Kommando {i}: deleteRange braucht from < to.")
                continue
            c["from"], c["to"] = von, bis
            c["ripple"] = bool(c.get("ripple", True))
            if c.get("tlIds"):
                c["tlIds"] = [x for x in c["tlIds"] if x in bekannte_tl_ids] or None
                if c["tlIds"] is None:
                    del c["tlIds"]
        elif typ == "move":
            if c.get("tlId") not in bekannte_tl_ids:
                fehler.append(f"Kommando {i}: move — tlId '{c.get('tlId')}' existiert nicht.")
                continue
            ns = _zeit(c.get("newStart"), "newStart", i)
            if ns is None:
                continue
            c["newStart"] = ns
        elif typ == "trim":
            if c.get("tlId") not in bekannte_tl_ids:
                fehler.append(f"Kommando {i}: trim — tlId '{c.get('tlId')}' existiert nicht.")
                continue
            if c.get("side") not in ("left", "right"):
                fehler.append(f"Kommando {i}: trim.side muss 'left' oder 'right' sein.")
                continue
            try:
                delta = float(c.get("delta"))
            except (TypeError, ValueError):
                fehler.append(f"Kommando {i}: trim.delta ist keine Zahl.")
                continue
            clip = je_id.get(c["tlId"]) or {}
            dauer = float(clip.get("duration") or 0.0)
            if delta < 0 and dauer and -delta >= dauer:
                warnungen.append(f"Kommando {i}: trim um {-delta:.1f}s würde den Clip ({dauer:.1f}s) auslöschen — auf −{max(0.0, dauer-0.5):.1f}s begrenzt.")
                delta = -max(0.0, dauer - 0.5)
            if abs(delta) > 120:
                warnungen.append(f"Kommando {i}: trim.delta {delta:.0f}s ist ungewöhnlich groß.")
            c["delta"] = delta
        elif typ == "insert":
            if not c.get("clipId"):
                fehler.append(f"Kommando {i}: insert braucht clipId (Quell-Clip).")
                continue
            at = _zeit(c.get("at"), "at", i)
            if at is None:
                continue
            c["at"] = at
            c["videoTrackIndex"] = int(c.get("videoTrackIndex") or 0)
            if c.get("mode") not in ("append", "insert", "overwrite"):
                c["mode"] = "insert"
            if c.get("duration") is not None:
                try:
                    c["duration"] = max(0.2, float(c["duration"]))
                except (TypeError, ValueError):
                    del c["duration"]
            if c.get("mediaStart") is not None:
                try:
                    c["mediaStart"] = max(0.0, float(c["mediaStart"]))
                except (TypeError, ValueError):
                    del c["mediaStart"]
            if c.get("videoOnly") is not None:
                c["videoOnly"] = bool(c["videoOnly"])
        elif typ == "setFade":
            if c.get("tlId") not in bekannte_tl_ids:
                fehler.append(f"Kommando {i}: setFade — tlId '{c.get('tlId')}' existiert nicht.")
                continue
            if c.get("side") not in ("in", "out"):
                fehler.append(f"Kommando {i}: setFade.side muss 'in' oder 'out' sein.")
                continue
            try:
                c["duration"] = min(10.0, max(0.0, float(c.get("duration"))))
            except (TypeError, ValueError):
                fehler.append(f"Kommando {i}: setFade.duration ist keine Zahl.")
                continue
        elif typ == "setGain":
            if c.get("tlId") not in bekannte_tl_ids:
                fehler.append(f"Kommando {i}: setGain — tlId '{c.get('tlId')}' existiert nicht.")
                continue
            try:
                c["gainDb"] = min(12.0, max(-60.0, float(c.get("gainDb"))))
            except (TypeError, ValueError):
                fehler.append(f"Kommando {i}: setGain.gainDb ist keine Zahl.")
                continue
        elif typ == "addMarker":
            at = _zeit(c.get("at"), "at", i)
            if at is None:
                continue
            c["at"] = at
            c["label"] = str(c.get("label") or "Marker")[:80]
        elif typ == "setRange":
            for k in ("inPoint", "outPoint"):
                if c.get(k) is not None:
                    z = _zeit(c.get(k), k, i)
                    if z is None:
                        continue
                    c[k] = z
                else:
                    c[k] = None
        elif typ == "loadSequence":
            segs = c.get("segments")
            if not isinstance(segs, list) or not segs:
                fehler.append(f"Kommando {i}: loadSequence ohne segments.")
                continue
            saubere = []
            for s_ in segs:
                if not isinstance(s_, dict) or not s_.get("clipId"):
                    continue
                try:
                    saubere.append({"clipId": str(s_["clipId"]), "mediaStart": max(0.0, float(s_.get("mediaStart") or 0)),
                                    "duration": max(0.2, float(s_.get("duration") or 0)),
                                    **({"name": str(s_["name"])[:80]} if s_.get("name") else {})})
                except (TypeError, ValueError):
                    continue
            if not saubere:
                fehler.append(f"Kommando {i}: loadSequence — kein gültiges Segment.")
                continue
            c["segments"] = saubere
            c["replace"] = bool(c.get("replace", False))
        ok.append(c)
    return ok, warnungen, fehler


def _kommando_beschreibung(cmds: list[dict]) -> str:
    teile = []
    for c in cmds:
        t = c["type"]
        if t == "trim":
            teile.append(f"trim {c['side']} {c['delta']:+.1f}s ({c['tlId'][:8]}…)")
        elif t == "deleteRange":
            teile.append(f"deleteRange {c['from']:.1f}–{c['to']:.1f}s")
        elif t == "delete":
            teile.append(f"delete {len(c['tlIds'])} Clip(s)")
        elif t == "split":
            teile.append(f"split @{c['at']:.1f}s")
        elif t == "move":
            teile.append(f"move → {c['newStart']:.1f}s")
        elif t == "insert":
            teile.append(f"insert {str(c['clipId'])[:12]} @{c['at']:.1f}s")
        elif t == "loadSequence":
            teile.append(f"loadSequence {len(c['segments'])} Segmente" + (" (ersetzt Timeline)" if c.get("replace") else ""))
        else:
            teile.append(t)
    return " · ".join(teile)


async def tool_edit_timeline(args: dict, db: AsyncSession) -> dict:
    tl_state = AKTUELLER_TL_STATE.get()
    cmds = args.get("commands")
    ok, warnungen, fehler = pruefe_timeline_kommandos(cmds, tl_state)
    # insert: Quell-Clip-Namen → UUID auflösen (braucht DB, deshalb hier statt in der puren Validierung)
    from backend.api.agent import _resolve_clip_ids
    for c in ok:
        if c["type"] == "insert":
            try:
                ids = await _resolve_clip_ids(db, [c["clipId"]])
                if ids:
                    c["clipId"] = ids[0]
                else:
                    fehler.append(f"insert: Quell-Clip '{c['clipId']}' nicht in der Bibliothek gefunden.")
                    ok = [x for x in ok if x is not c]
            except Exception as e:  # noqa: BLE001
                fehler.append(f"insert: Clip-Auflösung fehlgeschlagen ({e}).")
                ok = [x for x in ok if x is not c]
    if not ok:
        return {"ok": False, "fehler": fehler or ["Keine gültigen Kommandos."], "warnungen": warnungen,
                "hinweis": "Nichts vorgeschlagen. Prüfe tlIds (stehen im Timeline-Kontext) und Zeiten."}
    return {"ok": True, "proposal": True,
            "titel": str(args.get("titel") or "Timeline-Bearbeitung")[:80],
            "commands": ok, "warnungen": warnungen, "fehler": fehler,
            "zusammenfassung": _kommando_beschreibung(ok),
            "hinweis": "Als Vorschlag mit Geister-Vorschau an den Editor geschickt — der Nutzer muss ihn AKZEPTIEREN. "
                       "Nenne in final_answer GENAU: wie viele Kommandos, was sie tun, und alle Warnungen."}


def _plan_zu_segmenten(plan: Schnittplan, clips: dict) -> list[dict]:
    """Plan-Einträge → loadSequence-Segmente. Hauptspur sequenziell; Alternativen (Spur 2+) mit absoluter Position,
    eigener Spur und video_only — der Editor legt sie parallel über den Master. Ton-Brücken bleiben außen vor
    (Render-Detail, kommt beim Neuladen des persistierten Plans)."""
    segs = []
    for e in plan.eintraege or []:
        if e.get("audio_only"):
            continue
        d = float(e["out_s"]) - float(e["in_s"])
        if d <= 0:
            continue
        spur = int(e.get("spur") or 1)
        name = f"Sz{e['szene']} {e.get('einstellung') or ''} T{e.get('take') or '?'}" + (" · Alternative" if e.get("art") == "alternative" else "")
        seg = {"clip_id": e["clip_id"], "media_start": float(e["in_s"]), "duration": round(d, 3), "clip_name": name}
        if spur > 1 or e.get("art") == "alternative":
            seg["start"] = float(e.get("tl_start") or 0.0)
            seg["video_track_index"] = spur - 1
            seg["video_only"] = True
        segs.append(seg)
    return segs


async def tool_regenerate_schnittplan(args: dict, db: AsyncSession) -> dict:
    modus = str(args.get("modus") or "feinschnitt").lower()
    if modus not in ("rohschnitt", "feinschnitt"):
        return {"error": "modus: rohschnitt | feinschnitt"}
    name = str(args.get("name") or f"{modus.capitalize()} (KI-Agent)")[:120]

    def _lauf() -> dict:
        from backend.core.database import SyncSessionLocal
        from backend.core.skript.beats import berechne_takt
        from backend.core.skript.schnittplan import erzeuge_schnittplan
        sdb = SyncSessionLocal()
        try:
            sk = sdb.query(Skript).filter(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()).first()
            if sk is None:
                return {"error": "Kein aktives Drehbuch."}
            berechne_takt(sdb, sk, nur_fehlende=True)
            plan = erzeuge_schnittplan(sdb, sk, name, {"modus": modus})
            # Timeline persistieren wie der Worker (Auto-Restore beim Neuladen des Editors)
            try:
                clips_s = {str(c.id): c for c in sdb.query(Clip).all()}
                cursor = 0.0
                segs = []
                for e in plan.eintraege or []:
                    c = clips_s.get(e["clip_id"])
                    d = float(e["out_s"]) - float(e["in_s"])
                    if not c or d <= 0:
                        continue
                    start = float(e["tl_start"]) if e.get("tl_start") is not None else cursor
                    segs.append({"id": f"{e['clip_id']}-plan-{plan.id.hex[:6]}-{e['nr']}", "clip_id": e["clip_id"],
                                 "label": f"Sz{e['szene']} {e.get('einstellung') or ''} T{e.get('take') or '?'} · {c.dateiname.rsplit('.', 1)[0]}"
                                          + (" · Alternative" if e.get("art") == "alternative" else ""),
                                 "track": f"v{int(e.get('spur') or 1)}", "start": round(start, 3), "dauer": round(d, 3), "quelle": "A",
                                 "media_start": float(e["in_s"]), "source_duration": c.dauer,
                                 "video_only": bool(e.get("video_only")), "audio_only": bool(e.get("audio_only")),
                                 "alternative": e.get("art") == "alternative",
                                 "fade_in": float(e.get("fade_in") or 0), "fade_out": float(e.get("fade_out") or 0)})
                    if not e.get("audio_only") and int(e.get("spur") or 1) == 1:
                        cursor = start + d
                sdb.add(Timeline(id=uuid_mod.uuid4(), name=plan.name, stil="rohschnitt", prompt=f"schnittplan:{plan.id}",
                                 daten={"segmente": segs, "gesamtdauer": round(cursor, 3), "schnittplan_id": str(plan.id)},
                                 gesamtdauer=round(cursor, 3)))
                sdb.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Timeline-Persistenz fehlgeschlagen: {e}")
            st = plan.statistik or {}
            return {"plan_id": str(plan.id), "name": plan.name, "modus": modus,
                    "eintraege": st.get("eintraege"), "dauer_s": st.get("dauer_s"),
                    "luecken": (st.get("luecken") or [])[:10],
                    "segments": _plan_zu_segmenten(plan, {})}
        finally:
            sdb.close()

    res = await asyncio.to_thread(_lauf)
    if "error" in res:
        return res
    res["hinweis"] = ("Neuer Plan erzeugt und als Timeline-Vorschlag (Geister-Vorschau, ersetzt die aktuelle Timeline) "
                      "geschickt — der Nutzer muss AKZEPTIEREN. Nenne in final_answer: Segmentzahl, Dauer, Lücken.")
    return res


async def tool_lege_sequenzen_chronologisch(args: dict, db: AsyncSession) -> dict:
    """„Sequenzen chronologisch nach Skript“ — die Profi-Interpretation: je Szene EIN Segment in Skript-Reihenfolge,
    bester Take der Szene, getrimmt aufs Spielfenster (Klappe raus, Ausstieg raus). Läuft als Timeline-Vorschlag."""
    try:
        max_s = float(args.get("max_s_pro_szene")) if args.get("max_s_pro_szene") is not None else None
        if max_s is not None and max_s <= 0:
            max_s = None          # halluzinierte 0 (beobachtet) ⇒ kein Cap, nicht „0 Sekunden"
    except (TypeError, ValueError):
        max_s = None

    def _lauf() -> dict:
        from backend.core.config import PROXY_DIR
        from backend.core.database import SyncSessionLocal
        from backend.core.medien import clip_stem
        from backend.core.skript import aktivitaet as AK
        from backend.core.skript.beats import _spiel_grenzen
        from backend.core.skript.kontext import take_score
        from backend.core.skript.schnittplan import _einst_key

        def _fenster(tk: TakeKontext, c: Clip) -> tuple[float, float, str]:
            """Spielfenster + VISUELLE Klappe: die gesprochene Klappe endet früher als das Zuklappen der Tafel im Bild
            (und stumme Takes haben gar keine gesprochene) → zusätzlich Bewegungs-Spike am Anfang überspringen und
            Aus-dem-Spiel-Fallen am Ende abschneiden. Beobachtet 20.08.: ohne das war die Klappe im Schnitt sichtbar."""
            a, b = _spiel_grenzen(tk, c)
            # Schutz: Produktions-Sprech MITTEN im Take („Hallo?“ Richtung Crew) darf den Einstieg nicht hinter das
            # eigentliche Spiel schieben (beobachtet: 4.3 T1 → 38–39 s statt 20–39 s). Der Einstieg liegt nie hinter
            # der ersten Spiel-Äußerung.
            erste_spiel = min((float(it["start"]) for it in (tk.zeilen or []) if it.get("art") == "spiel"), default=None)
            if erste_spiel is not None and a > erste_spiel - 0.1:
                a = max(0.5, erste_spiel - 0.5)
            hinweis = f"nach gesprochener Klappe {a:.0f}s"
            proxy = PROXY_DIR / f"{clip_stem(c)}_proxy.mp4"
            if proxy.exists() and proxy.stat().st_size > 0:
                k = AK.kurve(str(proxy))
                if k:
                    a2, h = AK.anfang_nach_klappe(k, a)
                    if h and a2 < b - 1.0:
                        a, hinweis = a2, f"sichtbare Klappe übersprungen → {a2:.0f}s"
                    # Ausstieg (Aufstehen/Lachen) am Ende — aber höchstens 15 s: mehr wäre kein Ausstieg,
                    # sondern Spiel (beobachtet: 38-s-Schnitt hätte das Finale der Szene 2 gekostet)
                    b2, h2 = AK.ende_bereinigen(k, a, b)
                    if h2 and b2 > a + 1.0 and b - b2 <= 15.0:
                        b = b2
            # Der Einstieg liegt NIE hinter der ersten Spiel-Äußerung — auch nicht nach dem visuellen Klappen-Skip
            # (beobachtet: Bewegungs-Spike bei 21–24 s hätte „Geh doch an!“ @20 s halb verschluckt)
            if erste_spiel is not None and a > erste_spiel - 0.1:
                a = max(0.5, erste_spiel - 0.5)
            return max(a, 0.5), b, hinweis          # nie Frame 0: auch stumme Takes starten nach dem Einrichten

        sdb = SyncSessionLocal()
        try:
            sk = sdb.query(Skript).filter(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()).first()
            if sk is None:
                return {"error": "Kein aktives Drehbuch."}
            clips = {c.id: c for c in sdb.query(Clip).all()}
            szenen = sorted(sk.szenen, key=lambda z: z.reihenfolge)
            segs, wahl_protokoll, ohne_takes = [], [], []
            for sz in szenen:
                tks = [t for t in sdb.query(TakeKontext).filter(TakeKontext.skript_szene_id == sz.id).all()
                       if t.clip_id in clips]
                if not tks:
                    ohne_takes.append(sz.nummer)
                    continue
                hat_dialog = any(z.art == "dialog" for z in sz.zeilen)
                max_spiel = max(((t.spiel_ende_s or 0) - (t.spiel_start_s or 0)) for t in tks) or 1.0
                # ── Szene ohne Dialog (Inserts): gleiche Einstellungs-Nummer, aber VERSCHIEDENE Motive
                # (Fotorahmen, Tassen, Platten …) → Dedupe über das CLIP-Bild-Embedding: ein Segment je Motiv,
                # NICHT eins je Szene (sonst fehlen die Motive) und nicht eins je Take (sonst Wiederholungen).
                if not hat_dialog:
                    from backend.core.skript.schnittplan import _insert_cluster
                    for grp in _insert_cluster(tks, clips, sdb):
                        best = max(grp, key=lambda t: take_score(t, max_spiel)[0])
                        c = clips[best.clip_id]
                        a, b, hinweis = _fenster(best, c)
                        b = min(b, a + 5.0)                      # Insert-Länge: kurzer Blick reicht in der Sichtung
                        if b - a < 1.0:
                            continue
                        segs.append({"clip_id": str(c.id), "media_start": round(a, 3), "duration": round(b - a, 3),
                                     "clip_name": f"Sz{sz.nummer} Insert {best.einstellung or '?'} T{best.slate_take or '?'}"})
                        wahl_protokoll.append(f"Sz{sz.nummer} Insert: {c.dateiname.rsplit('.', 1)[0]} "
                                              f"(T{best.slate_take}, Motiv-Cluster aus {len(grp)} Takes), {a:.0f}–{b:.0f}s")
                    continue
                # ── Dialogszene: ein Segment je EINSTELLUNG (2.1, 2.2, 2.4 … = eigene Blickwinkel/Inhalte),
                # bester Take INNERHALB der Einstellung (T1/T2/… = Wiederholungen derselben Aufführung).
                # Reihenfolge: Szene → Teil → Einstellungs-Nummer (dreistufige Klappe 5.1.x/5.2.x bleibt chronologisch).
                je_einst: dict = {}
                for t in tks:
                    je_einst.setdefault(_einst_key(t.einstellung), []).append(t)
                for e_key in sorted(je_einst):
                    grp = je_einst[e_key]
                    kand = [t for t in grp if not (t.ng or {}).get("abbruch")] or grp
                    best = max(kand, key=lambda t: take_score(t, max_spiel)[0])
                    c = clips[best.clip_id]
                    a, b, hinweis = _fenster(best, c)
                    if max_s and b - a > max_s:
                        b = a + max_s
                    if b - a < 1.0:
                        continue
                    segs.append({"clip_id": str(c.id), "media_start": round(a, 3), "duration": round(b - a, 3),
                                 "clip_name": f"Sz{sz.nummer} {best.einstellung or '?'} T{best.slate_take or '?'}"})
                    wahl_protokoll.append(f"Sz{sz.nummer} Einstellung {best.einstellung}: T{best.slate_take} "
                                          f"({c.dateiname.rsplit('.', 1)[0]}, bester von {len(grp)}), {a:.0f}–{b:.0f}s ({hinweis})")
            if not segs:
                return {"error": "Keine Szene hat verwendbare Takes."}
            return {"segments": segs, "story_title": "Chronologische Sichtungs-Fassung",
                    "wahl": wahl_protokoll, "szenen_ohne_material": ohne_takes or None,
                    "dauer_s": round(sum(s_["duration"] for s_ in segs), 1)}
        finally:
            sdb.close()

    res = await asyncio.to_thread(_lauf)
    if "error" in res:
        return res
    res["annahmen"] = ("Klappe/Slate nie mitgeschnitten · EIN Segment je EINSTELLUNG (Takes derselben Einstellung sind "
                       "Wiederholungen — der beste zählt) · Szenen ohne Dialog: ein Segment je Motiv (Bild-Cluster) · "
                       "Szenen und Teile in Skript-Reihenfolge")
    res["hinweis"] = ("Als Timeline-Vorschlag geschickt (ersetzt die aktuelle Timeline nach Akzeptieren). "
                      "Nenne in final_answer die ANNAHMEN, die Take-Wahl je Szene und die Gesamtdauer.")
    return res


async def tool_lege_alternativen(args: dict, db: AsyncSession) -> dict:
    """Alternativen-Stapel auf Zuruf: beste Passagen ANDERER Takes je Beat des aktuellen Plans, stumm auf V2/V3+,
    am Beat des Masters ausgerichtet — als Vorschlag (insert-Kommandos, additiv zur bestehenden Timeline)."""
    szene = str(args.get("szene") or "").strip() or None
    beat = None
    try:
        if args.get("beat") is not None:
            beat = int(args.get("beat"))
    except (TypeError, ValueError):
        beat = None
    try:
        max_pro_beat = max(1, min(4, int(args.get("max_pro_beat") or 2)))
    except (TypeError, ValueError):
        max_pro_beat = 2
    # Auf die NÄCHSTE FREIE Spur über der aktuellen Timeline stapeln (liegt schon ein Alternativen-Stapel auf V2/V3,
    # landen neue auf V4+ statt in der Kollisions-Verschiebung ans Timeline-Ende)
    tl_state = AKTUELLER_TL_STATE.get() or {}
    belegte = [int(c.get("videoTrackIndex") or 0) for c in (tl_state.get("clips") or []) if isinstance(c, dict)]
    basis_spur = max(2, (max(belegte) + 2) if belegte else 2)          # 1-basiert: V2 wenn nur V1 belegt

    def _lauf() -> dict:
        from types import SimpleNamespace
        from backend.core.database import SyncSessionLocal
        from backend.core.skript.schnittplan import _takes_je_szene, alternativen_fuer_plan
        sdb = SyncSessionLocal()
        try:
            sk = sdb.query(Skript).filter(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()).first()
            plan = sdb.query(Schnittplan).order_by(Schnittplan.erstellt_am.desc()).first()
            if sk is None or plan is None:
                return {"error": "Kein aktives Drehbuch oder kein Schnittplan."}
            clips = {c.id: c for c in sdb.query(Clip).all()}
            eintraege = [SimpleNamespace(nr=e["nr"], szene=e["szene"], clip_id=e["clip_id"],
                                         art=e.get("art"), beats=e.get("beats") or [],
                                         spur=int(e.get("spur") or 1), tl_start=e.get("tl_start"),
                                         in_s=float(e["in_s"]), out_s=float(e["out_s"]))
                         for e in (plan.eintraege or [])]
            alt = alternativen_fuer_plan(eintraege, _takes_je_szene(sdb), clips, sk,
                                         max_pro_beat=max_pro_beat, nur_szene=szene, nur_beat=beat)
            cmds, gruende = [], []
            for a in alt:
                spur = basis_spur + (a.spur - 2)          # a.spur 2/3 → basis, basis+1
                cmds.append({"type": "insert", "clipId": a.clip_id, "at": round(a.tl_start or 0.0, 3),
                             "videoTrackIndex": spur - 1, "mode": "append", "videoOnly": True,
                             "duration": round(a.out_s - a.in_s, 3), "mediaStart": round(a.in_s, 3)})
                gruende.append(f"B{a.beats[0]} ← {a.einstellung} T{a.take} @{(a.tl_start or 0):.0f}s (V{spur}): {a.grund}")
            if not cmds:
                return {"ok": False, "hinweis": "Keine Alternativen gefunden (kein anderer Take belegt diese Beats mit Evidenz)."}
            return {"ok": True, "proposal": True, "titel": f"Alternativen auf V2+{'' if max_pro_beat < 2 else '/V3'}"
                    + (f" — Szene {szene}" if szene else "") + (f" Beat {beat}" if beat is not None else ""),
                    "commands": cmds, "gruende": gruende[:12],
                    "zusammenfassung": f"{len(cmds)} Alternative(n), stumm, am Beat ausgerichtet",
                    "hinweis": ("Vorschlag: Alternativen liegen PARALLEL über dem Master (V2/V3, ohne Ton). Der Nutzer "
                                "vergleicht per Spur-Ausblenden, behält die beste, löscht den Rest. Nenne in final_answer "
                                "die Anzahl und je Alternative Beat/Take/Grund.")}
        finally:
            sdb.close()

    return await asyncio.to_thread(_lauf)


async def tool_swap_beat_source(args: dict, db: AsyncSession) -> dict:
    szene = str(args.get("szene") or "").strip()
    try:
        beat = int(args.get("beat"))
    except (TypeError, ValueError):
        return {"error": "beat muss eine Zahl sein (Beat-Nummer aus get_scene_context / get_plan)."}
    tk, c = await _finde_take(db, str(args.get("take") or ""))
    if tk is None or c is None:
        return {"error": f"Ziel-Take '{args.get('take')}' nicht gefunden (Dateiname, UUID oder „2.1 T4“)."}
    span = next((sp for sp in (tk.takt or []) if int(sp.get("beat", -1)) == beat and sp.get("evidenz")), None) or \
        next((sp for sp in (tk.takt or []) if int(sp.get("beat", -1)) == beat), None)
    if span is None:
        return {"error": f"Take {c.dateiname} hat keinen Beat-Span B{beat} (get_scene_context zeigt, wer den Beat belegt)."}
    plan = await _letzter_plan(db)
    if plan is None:
        return {"error": "Kein Schnittplan vorhanden."}
    ziel = [e for e in (plan.eintraege or []) if e.get("szene") == szene and beat in (e.get("beats") or [])]
    if not ziel:
        return {"error": f"Im aktuellen Plan zeigt kein Segment der Szene {szene} den Beat B{beat}."}
    t_in, t_out = float(span["start"]), float(span["end"])
    if t_out - t_in > 60.0:
        t_out = t_in + 60.0
    segs = []
    ersetzt = {e["nr"] for e in ziel}
    for e in plan.eintraege or []:
        if e.get("audio_only"):
            continue
        if e["nr"] in ersetzt:
            # nur einmal einsetzen (beim ersten Treffer), weitere ersetzte Segmente fallen weg
            if e["nr"] == min(ersetzt):
                segs.append({"clip_id": str(c.id), "media_start": round(t_in, 3), "duration": round(t_out - t_in, 3),
                             "clip_name": f"Sz{szene} {tk.einstellung} T{tk.slate_take} (getauscht B{beat})"})
            continue
        d = float(e["out_s"]) - float(e["in_s"])
        if d <= 0:
            continue
        segs.append({"clip_id": e["clip_id"], "media_start": float(e["in_s"]), "duration": round(d, 3),
                     "clip_name": f"Sz{e['szene']} {e.get('einstellung') or ''} T{e.get('take') or '?'}"})
    alte = ", ".join(f"Nr{e['nr']} {e.get('einstellung')} T{e.get('take')} ({e['in_s']:.0f}–{e['out_s']:.0f}s)" for e in ziel)
    return {"ok": True, "segments": segs, "story_title": f"B{beat} ← {tk.einstellung} T{tk.slate_take}",
            "ersetzt": alte, "neu": f"{c.dateiname} {t_in:.1f}–{t_out:.1f}s",
            "warnung_beats": [b for e in ziel for b in (e.get("beats") or []) if b != beat] or None,
            "hinweis": "Ganze Timeline als Vorschlag neu geladen (ersetzt), mit der neuen Quelle für diesen Beat. "
                       "warnung_beats = weitere Beats, die im ersetzten Segment lagen und mit getauscht wurden."}
