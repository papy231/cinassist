"""L5 — Schnittplan (Rohschnitt) aus Skript + Take-/Szenen-Kontext. Regelbasiert, jede Entscheidung begründet.

Prinzip Rohschnitt: Skript-Reihenfolge · je Szene ein „Master“ (Einstellung mit der höchsten Skript-Abdeckung) als
Rückgrat · Einstellungswechsel nur an Sprecherwechseln, wenn eine andere Einstellung die Zeile deckt (klassische
Dialog-Coverage) · zusammenhängende Zeilen desselben Takes werden zu EINEM Segment verschmolzen (keine Hektik) ·
Zeilen ohne Treffer werden als Lücke gemeldet · stumme Einstellungen (Handlung ohne Dialog) kommen in Klappen-
Reihenfolge an ihre Position (vor/zwischen/nach den Dialog-Einstellungen) · Inserts (Szene ohne Dialog, kurze
Clips) werden den Skript-Erwähnungen per Embedding zugeordnet.
Kein LLM. Output = Einträge {clip_id, in_s, out_s, …, grund, beleg} + Statistik.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from backend.core.config import PROXY_DIR
from backend.core.database import Clip, Skript, SkriptSzene, TakeKontext, SzenenKontext, Schnittplan, Szene
from backend.core.skript import alignment as A
from backend.core.skript import aktivitaet as AK
from backend.core.skript.kontext import take_score
from backend.core.medien import clip_stem

HANDLE_VOR = 0.5      # s vor dem ersten Wort
HANDLE_NACH = 0.8     # s nach dem letzten Wort
LUECKE_MAX = 6.0      # s: Pause zwischen zwei Zeilen desselben Takes, die noch als Handlung mitgenommen wird
AKTION_LUECKE_MAX = 45.0  # s: … wenn das Skript dazwischen eine Aktion hat und der Take dort Bewegung zeigt
INSERT_DAUER = 3.5    # s je Insert, wenn der Clip länger ist
STUMM_MAX = 75.0      # s Obergrenze je stumme Einstellung im Rohschnitt
STATISCH_MAX = 10.0   # s für Einstellungen ohne messbare Bewegung (Stillleben/Detail)
MAX_SEGMENT_S = 28.0  # Rhythmus: länger als das bleibt der Rohschnitt nicht auf einer Einstellung, wenn Coverage da ist
FRAMING_RANG = {"extreme_closeup": 4, "closeup": 3, "medium": 2, "wide_with_person": 1, "wide_no_person": 0}


@dataclass
class Eintrag:
    nr: int
    szene: str
    clip_id: str
    dateiname: str
    einstellung: str | None
    take: int | None
    in_s: float
    out_s: float
    zeilen: list[int] = field(default_factory=list)
    art: str = "dialog"            # dialog | stumm | insert | cutaway | audio
    grund: str = ""
    beleg: list[str] = field(default_factory=list)
    video_only: bool = False       # Cutaway: Bild ohne Ton (der Ton läuft vom Master weiter)
    audio_only: bool = False       # Ton-Brücke: nur der Ton des Masters während eines Cutaways
    fade_in: float = 0.0
    fade_out: float = 0.0
    tl_start: float | None = None  # absolute Position auf der Timeline (vom Generator gesetzt)
    spur: int = 1                  # Video-Spur: 1 = Master (rough cut); 2 = Schnitt-Overlays (Cutaways/Reaktionen); 3+ = Alternativen
    overlay_offset: float | None = None   # Overlay: Position relativ zum Anfang des Parent-Master-Segments (tl wird daraus abgeleitet)
    eroeffnung: bool = False       # Szenen-Eröffnung im selben Take (Regel 1): Einstieg ist gesetzt, kein weiterer Vorlauf/Klappen-Skip
    beats: list[int] = field(default_factory=list)   # Beat-Nummern (Szenen-Takt), die dieses Segment zeigt

    @property
    def dauer(self) -> float:
        return round(self.out_s - self.in_s, 3)

    def als_dict(self) -> dict:
        return {"nr": self.nr, "szene": self.szene, "clip_id": self.clip_id, "dateiname": self.dateiname,
                "einstellung": self.einstellung, "take": self.take, "in_s": round(self.in_s, 3), "out_s": round(self.out_s, 3),
                "dauer": self.dauer, "zeilen": self.zeilen, "art": self.art, "grund": self.grund, "beleg": self.beleg,
                "video_only": self.video_only, "audio_only": self.audio_only, "fade_in": self.fade_in, "fade_out": self.fade_out,
                "tl_start": (round(self.tl_start, 3) if self.tl_start is not None else None), "beats": self.beats,
                "spur": self.spur}


def _takes_je_szene(db) -> dict:
    out = defaultdict(list)
    for tk in db.query(TakeKontext).all():
        if tk.skript_szene_id:
            out[tk.skript_szene_id].append(tk)
    return out


def _bester_take(tks: list[TakeKontext], max_spiel: float, erlaubt_abbruch: bool = False) -> TakeKontext | None:
    kand = [t for t in tks if erlaubt_abbruch or not (t.ng or {}).get("abbruch")] or tks
    if not kand:
        return None
    return max(kand, key=lambda t: take_score(t, max_spiel)[0])


def _zeilen_zeiten(tk: TakeKontext) -> dict[str, list[tuple[float, float, str, float]]]:
    """skript_zeile_id → [(start, end, text, score)] der zugeordneten Sätze."""
    out: dict[str, list[tuple[float, float, str, float]]] = defaultdict(list)
    for it in tk.zeilen or []:
        if it.get("art") == "spiel" and it.get("skript_zeile_id"):
            out[it["skript_zeile_id"]].append((float(it["start"]), float(it["end"]), it["text"], float(it.get("score") or 0.0)))
    return out


WECHSEL_MIN_SCORE = 0.62   # um die Master-Einstellung zu verlassen, muss der Zeilen-Treffer der anderen Einstellung sicher sein


def _spiel_fenster(tk: TakeKontext, clip: Clip, stumm_max: float = STUMM_MAX) -> tuple[float, float, str]:
    """Für stumme Takes: Fenster = nach Klappe/Produktions-Sprech bis kurz vor Schluss, dann per **Aktivitätskurve**
    (Bildbewegung aus dem Proxy) auf die Handlung getrimmt: Einrichten/Stillstand am Ende fallen weg; ist das aktive
    Stück länger als `stumm_max`, wird das aktivste Fenster dieser Länge gewählt; ohne messbare Bewegung (Stillleben)
    bleiben ≤ STATISCH_MAX s. Liefert (in, out, begründung)."""
    dauer = float(clip.dauer or 0.0)
    # Letzter Slate-/Produktions-Sprech VOR der ersten Spiel-Äußerung = harte Grenze (Anspielen davor gehört nie in
    # den Schnitt); ohne Spiel-Äußerung gilt die Erste-Hälfte-Regel (s. beats._spiel_grenzen, Befund 3.2/T3 „Bitte.").
    erste_spiel = min((float(it["start"]) for it in (tk.zeilen or []) if it.get("art") == "spiel"), default=None)
    start = 0.0
    for it in tk.zeilen or []:
        if it.get("art") not in ("slate", "produktion"):
            continue
        ende_it = float(it["end"])
        if (erste_spiel is not None and ende_it <= erste_spiel) or (erste_spiel is None and ende_it < dauer * 0.5):
            start = max(start, ende_it + 0.5)
    if tk.spiel_start_s is not None:
        start = min(start, tk.spiel_start_s) if start else tk.spiel_start_s
    if erste_spiel is not None and start > erste_spiel - 0.1:
        start = max(0.0, erste_spiel - 0.5)
    ende = dauer - 1.0
    for it in tk.zeilen or []:
        if it.get("art") == "produktion" and float(it["start"]) > dauer * 0.7:
            ende = min(ende, float(it["start"]) - 0.3)
    start, ende = max(0.0, start), max(start + 1.0, ende)
    grund = "Fenster nach Klappe bis Schluss"
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if proxy.exists() and proxy.stat().st_size > 0:
        k = AK.kurve(str(proxy))
        af = AK.aktives_fenster(k)
        if af is not None:
            a0, a1 = max(af[0], start), min(af[1], ende)
            if a1 - a0 < 4.0:
                ende = min(ende, start + STATISCH_MAX)
                grund = f"kaum Bildbewegung (statisch) → {STATISCH_MAX:.0f} s"
            else:
                if a1 - a0 > stumm_max:
                    bf = AK.bestes_fenster(k, stumm_max, (a0, a1))
                    if bf:
                        a0, a1 = max(start, bf[0]), min(ende, bf[1])
                    grund = f"aktivstes Stück {a0:.0f}–{a1:.0f} s (Bildbewegung, Obergrenze {stumm_max:.0f} s)"
                else:
                    grund = f"Handlung {a0:.0f}–{a1:.0f} s (Bildbewegung), Einrichten/Stillstand weggelassen"
                start, ende = max(start, a0 - 0.5), min(ende, a1 + 1.0)
        else:
            ende = min(ende, start + STATISCH_MAX)
            grund = f"keine Bildbewegung messbar → {STATISCH_MAX:.0f} s"
        # Sichtbare Klappe/Hand am Anfang und Aus-dem-Spiel-Fallen am Ende
        if start < 20.0:
            start2, h = AK.anfang_nach_klappe(k, start)
            if h and start2 < ende - 1.0:
                start = start2; grund += f" · {h}"
        ende2, h2 = AK.ende_bereinigen(k, start, ende)
        if h2:
            ende = ende2; grund += f" · {h2}"
    if ende - start > stumm_max:
        ende = start + stumm_max
    return max(0.0, start), max(start + 1.0, ende), grund


IMPROV_MAX_S = 30.0    # Regel 2: improvisierte Spiel-Sätze bis so viele Sekunden VOR der ersten alignierten Zeile gehören zum Segment
EROEFFNUNG_MIN_S = 8.0 # Regel 1: so viel Spiel vor der ersten Zeile muss der Master-Take haben, damit die Eröffnung aus ihm kommt
EROEFFNUNG_RUHE_S = 6.0  # Regel 1: so viel Stille (sie schläft) vor der ersten Bewegung (Wecker/Aufwachen) bleibt drin


def _ist_klappe_text(text: str) -> bool:
    t = (text or "").strip()
    return bool(re.match(r"^[\s\d.,:/-]+$", t)) or bool(re.match(r"^\s*\d+\s*[.,]\s*\d*", t)) or len(t) == 0


def _improvisation_davor(tk: TakeKontext, t_in: float, max_s: float = IMPROV_MAX_S) -> tuple[float | None, list[str]]:
    """Regel 2: nicht-alignierte Spiel-Sätze desselben Takes unmittelbar vor `t_in` („Oh, Scheiße.“ · „Babe?“ vor
    „Ich geh uns … Tee machen“) sind Spiel, kein Müll. Rückwärts gesammelt bis Slate/Produktions-Sprech, bis zu einer
    Zeile, die einer ANDEREN Skriptzeile gehört, oder bis `max_s`. Liefert (neuer Einstieg, Texte)."""
    items = sorted((i for i in (tk.zeilen or []) if float(i["end"]) <= t_in + 0.2 and float(i["start"]) >= t_in - max_s),
                   key=lambda i: float(i["start"]))
    start: float | None = None
    texte: list[str] = []
    for i in reversed(items):
        if i.get("art") != "spiel" or i.get("skript_zeile_id") or _ist_klappe_text(i.get("text") or ""):
            break
        if tk.spiel_start_s is not None and float(i["start"]) < tk.spiel_start_s - 0.2:
            break
        start = float(i["start"]); texte.insert(0, str(i.get("text") or "").strip())
    return start, texte


def _szenen_eroeffnung(clip: Clip, e: "Eintrag", tk: TakeKontext, stumm_max: float) -> str | None:
    """Regel 1: Hat der Take des ERSTEN Dialog-Segments lange Spielzeit vor der ersten Zeile (Schlafen → Wecker →
    Aufwachen → erste Worte), kommt die Eröffnungs-Aktion aus DIESEM Take, nicht aus einer anderen Einstellung:
    Einstieg = erste nachhaltige Bewegung minus EROEFFNUNG_RUHE_S (Stille davor), frühestens nach der Klappe.
    Verändert e.in_s; liefert die Begründung (oder None, wenn die Regel nicht greift)."""
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not (proxy.exists() and proxy.stat().st_size > 0):
        return None
    dauer = float(clip.dauer or 0.0)
    spiel_start = float(tk.spiel_start_s) if tk.spiel_start_s is not None else 0.0
    for it in tk.zeilen or []:
        if (it.get("art") in ("slate", "produktion") or _ist_klappe_text(it.get("text") or "")) and float(it["end"]) < dauer * 0.5 and float(it["end"]) < e.in_s:
            spiel_start = max(spiel_start, float(it["end"]) + 0.3)
    k = AK.kurve(str(proxy))
    if not k:
        return None
    nach_klappe, h = AK.anfang_nach_klappe(k, spiel_start)
    if h and nach_klappe < e.in_s - 2.0:
        spiel_start = max(spiel_start, nach_klappe)
    if e.in_s - spiel_start < EROEFFNUNG_MIN_S:
        return None
    region = [(t, v) for t, v in k if spiel_start <= t <= e.in_s]
    af = AK.aktives_fenster(region, mindest_s=1.5)
    if af is not None:
        t_bew = af[0]
        start = max(spiel_start, t_bew - EROEFFNUNG_RUHE_S)
        wie = f"erste Bewegung bei {t_bew:.0f} s, davor {t_bew - start:.0f} s Ruhe"
    else:
        start = max(spiel_start, e.in_s - 12.0)
        wie = "keine messbare Bewegung davor → 12 s Einstieg"
    if e.in_s - start > stumm_max:
        start = e.in_s - stumm_max
        wie += f", auf {stumm_max:.0f} s gekappt"
    if e.in_s - start < 2.0:
        return None
    alt = e.in_s
    e.in_s = round(start, 2)
    e.eroeffnung = True
    return f"Szenen-Eröffnung im selben Take: Spiel ab {spiel_start:.0f} s, {wie} → Einstieg {e.in_s:.0f} s statt {alt:.0f} s"


def _vor_nachlauf(clip: Clip, e: "Eintrag", tk: TakeKontext | None, sz: SkriptSzene, dialog: list, fein: bool) -> None:
    """Dramaturgische Atempause: steht im Skript VOR der ersten bzw. NACH der letzten Zeile des Segments eine Aktion
    („Orpheus gets up, kisses her … goes to the kitchen“), und der Take zeigt dort Bewegung, läuft das Segment mit —
    bis die Bewegung abklingt, bis zur nächsten fremden Zeile/Produktions-Sprech, höchstens NACHLAUF_MAX."""
    if tk is None or not e.zeilen:
        return
    aktionen = [z for z in sz.zeilen if z.art == "aktion"]
    dialog_nrs = sorted(z.nr for z in dialog)
    erste, letzte = min(e.zeilen), max(e.zeilen)
    vorher = [d for d in dialog_nrs if d < erste]
    nachher = [d for d in dialog_nrs if d > letzte]
    aktion_davor = any((vorher[-1] if vorher else -1) < a.nr < erste for a in aktionen) and not e.eroeffnung
    aktion_danach = any(letzte < a.nr < (nachher[0] if nachher else 10**6) for a in aktionen)
    if not (aktion_davor or aktion_danach):
        return
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not (proxy.exists() and proxy.stat().st_size > 0):
        return
    k = AK.kurve(str(proxy))
    if not k:
        return
    alle = sorted(v for _, v in k); med = alle[len(alle) // 2]
    schwelle = max(0.8, med * 1.3)
    grenzen = [(float(i["start"]), float(i["end"]), i.get("art"), i.get("skript_zeile_nr")) for i in (tk.zeilen or [])]
    max_s = 10.0 if fein else 18.0
    dauer = float(clip.dauer or 0.0)
    if aktion_danach:
        # bis zur nächsten Einheit einer ANDEREN Zeile / Produktions-Sprech, und solange Bewegung da ist
        stopp = min([a for a, _, art, nr_ in grenzen if a > e.out_s + 0.3 and (art != "spiel" or (nr_ is not None and nr_ not in e.zeilen))] + [dauer - 0.5])
        t = e.out_s
        ruhig = 0
        while t + 0.5 <= min(stopp, e.out_s + max_s):
            v = [vv for tt, vv in k if t <= tt < t + 0.5]
            if v and max(v) >= schwelle:
                ruhig = 0
            else:
                ruhig += 1
                if ruhig >= 4:          # 2 s ohne Bewegung → Ende der Aktion
                    break
            t += 0.5
        neu = max(e.out_s, t - (ruhig * 0.5) + 0.6)
        if neu - e.out_s >= 1.5:
            e.beleg.append(f"Nachlauf {neu - e.out_s:.1f} s: Skript-Aktion nach Z{letzte}, Bewegung im Bild")
            e.out_s = min(neu, stopp)
    if aktion_davor:
        stopp = max([b for _, b, art, nr_ in grenzen if b < e.in_s - 0.3 and (art != "spiel" or (nr_ is not None and nr_ not in e.zeilen))] + [0.5])
        # Regel 3: Sprache im Fenster (auch improvisiert) zählt als Aktivität, und bis 3 s Ruhe unterbrechen die Aktion nicht
        sprache = [(a, b) for a, b, art, nr_ in grenzen if art == "spiel" and nr_ is None]
        t = e.in_s
        ruhig = 0
        while t - 0.5 >= max(stopp, e.in_s - max_s):
            v = [vv for tt, vv in k if t - 0.5 <= tt < t]
            spricht = any(a < t and b > t - 0.5 for a, b in sprache)
            if (v and max(v) >= schwelle) or spricht:
                ruhig = 0
            else:
                ruhig += 1
                if ruhig >= 6:
                    break
            t -= 0.5
        neu = min(e.in_s, t + (ruhig * 0.5) - 0.6)
        if e.in_s - neu >= 1.5:
            e.beleg.append(f"Vorlauf {e.in_s - neu:.1f} s: Skript-Aktion vor Z{erste}, Bewegung im Bild")
            e.in_s = max(neu, stopp)


_KLAPPE_EMB = None


def _klappe_sichtbar_bis(clip: Clip, max_s: float = 15.0) -> float | None:
    """Sichtbare Klappe per CLIP-Zweitsignal: die ersten Frames werden gegen „Filmklappe vor der
    Kamera“ vs. „Raum ohne Equipment“ eingebettet — nötig, weil die Bewegungs-Heuristik auf
    statischen Detail-Clips (Szene-1-Inserts ohne WAV) blind ist. Liefert das Ende der letzten
    Klappen-Sichtung (+0,8 s Atem) oder None. Frames teilen den VQA-Cache."""
    global _KLAPPE_EMB
    try:
        import subprocess
        import numpy as np
        from backend.core import clip_encoder as CE
        from backend.core.skript.aktionen import VQA_DIR, FFMPEG_BIN, BREITE
    except Exception:  # noqa: BLE001
        return None
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not (proxy.exists() and proxy.stat().st_size > 0):
        return None
    try:
        if _KLAPPE_EMB is None:
            _KLAPPE_EMB = (CE.embed_text("a film clapperboard slate held up in front of the camera"),
                           CE.embed_text("an indoor scene without any film equipment"))
        pos, neg = _KLAPPE_EMB
        frames_dir = VQA_DIR / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        letzte = None
        t = 0.4
        grenze = min(max_s, float(clip.dauer or 0.0) - 0.5)
        while t < grenze:
            fp = frames_dir / f"{proxy.stem}_{int(t * 10):06d}.jpg"
            if not fp.exists():
                subprocess.run([FFMPEG_BIN, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(proxy),
                                "-frames:v", "1", "-q:v", "3", "-vf", f"scale={BREITE}:-2", str(fp)],
                               capture_output=True, timeout=60)
            if fp.exists() and fp.stat().st_size > 0:
                fe = CE.embed_image(fp)
                if float(np.dot(fe, pos)) > float(np.dot(fe, neg)) + 0.02:
                    letzte = t
                elif letzte is not None:
                    break     # Klappe war da und ist wieder weg
            t += 1.2
        return (letzte + 0.8) if letzte is not None else None
    except Exception:  # noqa: BLE001 — Klappen-Check darf den Plan nie stoppen
        return None


def _dialog_segment_bereinigen(clip: Clip, e: "Eintrag", nur_anfang: bool = False) -> None:
    """Dialog-Segment: liegt das Ende nahe am Take-Ende (letzte 15 s), Bewegungssprung (Aufstehen/Lachen) abschneiden;
    beginnt es in den ersten 20 s, sichtbare Klappe überspringen — ohne aligned Zeilen zu verlieren."""
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not (proxy.exists() and proxy.stat().st_size > 0):
        return
    k = AK.kurve(str(proxy))
    dauer = float(clip.dauer or 0.0)
    if e.in_s < 20.0 and not e.eroeffnung:
        s2, h = AK.anfang_nach_klappe(k, e.in_s)
        if h and s2 < e.out_s - 1.0 and s2 - e.in_s <= 6.0:
            e.in_s = s2; e.beleg.append(h)
        kv = _klappe_sichtbar_bis(clip)
        if kv is not None and e.in_s < kv < e.out_s - 1.0:
            e.beleg.append(f"sichtbare Klappe (CLIP-Bildcheck) bis {kv:.1f} s übersprungen")
            e.in_s = kv
    if not nur_anfang and dauer and e.out_s >= dauer - 15.0:
        o2, h2 = AK.ende_bereinigen(k, e.in_s, e.out_s, ab_anteil=0.6)
        if h2 and o2 > e.in_s + 1.0:
            e.out_s = o2; e.beleg.append(h2)


def _bewegung_im_fenster(clip: Clip, a: float, b: float) -> bool:
    """Zeigt der Take zwischen a und b Bewegung (Handlung), oder steht das Bild still?"""
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not (proxy.exists() and proxy.stat().st_size > 0) or b - a < 1.0:
        return False
    k = AK.kurve(str(proxy))
    vals = [v for t, v in k if a <= t <= b]
    alle = [v for _, v in k]
    if not vals or not alle:
        return False
    med = sorted(alle)[len(alle) // 2]
    return sum(1 for v in vals if v > max(0.6, med)) >= 0.3 * len(vals)


def _aktions_fenster(tk: TakeKontext, sz: SkriptSzene, t_in: float, t_out: float, max_s: float, fein: bool, clip: Clip | None = None) -> list[tuple[float, float, int, str]]:
    """Aus der Bildprüfung (tk.aktionen): je bestätigter Skript-Aktion ein Fenster im Take, in Skript-Reihenfolge;
    Feinschnitt kappt jedes Fenster auf 12 s um die Mitte, Rohschnitt auf max_s. Eine im Bild BESTÄTIGTE Aktion hat
    Vorrang vor dem Bewegungsfenster: Grenzen = nach Klappe/Produktions-Sprech bis kurz vor Schluss (statische
    Detailaufnahmen haben kaum Bewegung, sind aber genau das, was das Skript verlangt)."""
    akt = tk.aktionen or {}
    if not akt:
        return []
    if clip is not None:
        dauer = float(clip.dauer or 0.0)
        grenze_start = 0.0
        for it in tk.zeilen or []:
            if it.get("art") in ("slate", "produktion") and float(it["end"]) < dauer * 0.5:
                grenze_start = max(grenze_start, float(it["end"]) + 0.5)
        t_in = min(t_in, grenze_start) if grenze_start else min(t_in, 1.5)
        t_in = max(t_in, grenze_start)
        t_out = max(t_out, dauer - 0.5)
    texte = {str(z.nr): z.text for z in sz.zeilen if z.art == "aktion"}
    out: list[tuple[float, float, int, str]] = []
    # Bei Überlappung gewinnt die Aktion mit mehr Bild-Belegen (ja-Frames); Ausgabe danach in Skript-Reihenfolge
    for nr_s, info in sorted(akt.items(), key=lambda kv: -int(kv[1].get("ja") or 0)):
        spans = info.get("spans") or []
        schritt = float(info.get("schritt") or 5.0)
        # Einzeltreffer (ein Frame) sind Rauschen — mindestens zwei aufeinanderfolgende Ja-Frames
        spans = [sp for sp in spans if sp[1] - sp[0] >= schritt * 1.5]
        if not spans:
            continue
        # größtes Span dieser Aktion, ins Spielfenster geschnitten
        a, b = max(spans, key=lambda sp: sp[1] - sp[0])
        a, b = max(a, t_in), min(b, t_out)
        if b - a < 2.0:
            continue
        cap = 12.0 if fein else max_s
        if b - a > cap:
            m = (a + b) / 2; a, b = m - cap / 2, m + cap / 2
        # Überlappung mit bereits gelegten Fenstern vermeiden
        if any(not (b <= x0 or a >= x1) for x0, x1, _, _ in out):
            continue
        out.append((round(a, 2), round(b, 2), int(nr_s), texte.get(nr_s, "")))
    out.sort(key=lambda x: x[2])
    return out


def _stumm_key(sortkey: tuple, k: tuple, dialog_nums: list, dialog_einst, beste: dict) -> tuple:
    """Sortierschlüssel einer stummen Einstellung relativ zu den Dialog-Einstellungen: vor der ersten → vor Zeile 0,
    nach der letzten → ans Ende, sonst nach den Zeilen der vorangehenden Dialog-Einstellung."""
    if not dialog_nums or k < dialog_nums[0]:
        return (-1, 1, sortkey)
    if k > dialog_nums[-1]:
        return (10**6, 1, sortkey)
    vor = max(d for d in dialog_nums if d < k)
    vor_e = next(x for x in dialog_einst if _einst_key(x) == vor)
    letzte_nr = max((i.get("skript_zeile_nr") or 0) for i in (beste[vor_e].zeilen or []) if i.get("skript_zeile_id")) if beste[vor_e].zeilen else 0
    return (letzte_nr, 1, sortkey)


def _einst_key(e: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", e or "")) or (999,)


BEAT_CAP_DIALOG = {True: 45.0, False: 60.0}    # fein / roh: Obergrenze je Dialog-Beat-Segment
BEAT_CAP_AKTION = {True: 12.0, False: 20.0}    # fein / roh: Obergrenze je reinem Bild-Beat (VQA)


def _plane_szene_beats(sz: SkriptSzene, tks: list[TakeKontext], clips: dict, beste: dict, je_einst: dict, framing_je_clip: dict,
                       parameter: dict, fein: bool, nr: int, luecken: list, max_spiel: float,
                       ) -> tuple[list[tuple[tuple, "Eintrag"]], int, set[int], list[str]]:
    """Schnitt nach Beats (Szenen-Takt): je Beat genau eine Quelle, genau einmal; aufeinanderfolgende Beats desselben Takes
    werden zu einem Segment verschmolzen; Take-Wechsel nur an Beat-Grenzen, Einstieg in den neuen Take am Beginn des
    Beats (Phasen-Kontinuität). Liefert (segmente, nr, im-Bild-gedeckte Aktionen, Protokoll)."""
    from backend.core.skript.beats import beats_fuer_szene
    beats = beats_fuer_szene(sz)
    protokoll: list[str] = []
    if not beats:
        return [], nr, set(), protokoll
    max_segment = float(parameter.get("max_segment_s", MAX_SEGMENT_S))
    takt: dict = {}                                   # tk.id → {beat_nr: span}
    for tk in tks:
        if tk.takt:
            takt[tk.id] = {int(sp["beat"]): sp for sp in tk.takt if sp.get("evidenz")}
    # Einstellung „dediziert“: ihre Takes belegen nur 1–3 Dialog-Beats mit Anker (Detail/Gegenschuss für genau diese Zeilen)
    anker_beats_je_einst: dict[str, set[int]] = defaultdict(set)
    beats_je_einst: dict[str, set[int]] = defaultdict(set)          # alle durchlaufenen Beats (auch ohne Evidenz)
    for tk in tks:
        for b, sp in takt.get(tk.id, {}).items():
            if sp.get("anker"):
                anker_beats_je_einst[tk.einstellung or "?"].add(b)
        for sp in (tk.takt or []):
            beats_je_einst[tk.einstellung or "?"].add(int(sp["beat"]))
    n_dialog_beats = sum(1 for b in beats if b.art == "dialog")
    def dediziert(e_: str) -> bool:
        # Detail/Gegenschuss „für genau diese Zeilen“: die Einstellung durchläuft höchstens 3 Beats und weniger als die Hälfte
        return 1 <= len(beats_je_einst.get(e_, ())) <= 3 and len(beats_je_einst.get(e_, ())) * 2 < max(2, len(beats))
    # Szenen-Teile (dreistufige Klappe 5.1.x / 5.2.x): narrativ monoton, kein Rücksprung
    dreistufig = any(len(_einst_key(t.einstellung)) >= 3 for t in tks)
    teil = lambda t: (_einst_key(t.einstellung)[:2] if dreistufig else ())   # noqa: E731
    hoechster_teil: tuple = ()
    rang = lambda tk: FRAMING_RANG.get(framing_je_clip.get(tk.clip_id), 0)   # noqa: E731
    # ── Wiederholungs-Wächter: „dasselbe wird nie zweimal gesagt“ ──
    # Substanzielle Sätze (keine Rufe) jedes GEWÄHLTEN Segments werden gemerkt; ein Kandidat aus einem ANDEREN Take,
    # dessen Sätze mehrheitlich schon zu hören waren (gleiche Aufführung, anderer Winkel), wird übersprungen —
    # unabhängig davon, wie die Beat-Grenzen lagen (Sicherheitsnetz gegen Alignment-/Grenzfehler).
    from backend.core.skript.beats import _ist_kurzruf, _ist_klappe
    gehoert: list[tuple[str, object]] = []     # (satz, take_id)

    def _phrasen(tk_: TakeKontext, a: float, bb: float, ausser_zeilen: set | None = None) -> list[str]:
        out = []
        for it in tk_.zeilen or []:
            if it.get("art") != "spiel":
                continue
            t = str(it.get("text") or "").strip()
            if not t or _ist_kurzruf(t) or _ist_klappe(t):
                continue
            # Sätze, die auf eine Zeile DIESES Beats aligniert sind, sind der erwartete Text des Beats — sie zählen
            # nicht als „Wiederholung“ (sonst blockt „Das kann nicht sein“ (Z15) an „Das kann doch nicht sein!“ (B2))
            if ausser_zeilen and it.get("skript_zeile_nr") is not None and int(it["skript_zeile_nr"]) in ausser_zeilen:
                continue
            if float(it["start"]) >= a - 0.2 and float(it["end"]) <= bb + 0.2:
                out.append(t)
        return out

    def _schon_gehoert(texte: list[str], take_id) -> tuple[float, list[tuple[str, str]]]:
        rel = [t for t, tid in gehoert if tid != take_id]
        if not texte or not rel:
            return 0.0, []
        embs = A.embed(texte + rel)
        et, er = embs[:len(texte)], embs[len(texte):]
        dopp: list[tuple[str, str]] = []
        for t, e in zip(texte, et):
            for t2, e2 in zip(rel, er):
                if A.lexikalisch(t, t2) >= 0.7 or (e is not None and e2 is not None and A._cos(e, e2) >= 0.85):
                    dopp.append((t, t2))
                    break
        return len(dopp) / len(texte), dopp
    beste_ids = {t.id for t in beste.values()}
    segmente: list[tuple[tuple, Eintrag]] = []
    offen: Eintrag | None = None
    aktuell: TakeKontext | None = None
    seit_start: float | None = None                   # in_s des laufenden Segments (Rhythmus)
    letzte_figur = None
    gedeckte_aktionen: set[int] = set()
    for b in beats:
        kand: list[tuple[float, TakeKontext, dict, list[str]]] = []
        zurueck: list[str] = []
        for tk in tks:
            sp = takt.get(tk.id, {}).get(b.nr)
            if not sp or tk.clip_id not in clips:
                continue
            if b.art == "dialog" and not (sp.get("anker") or sp.get("weich") or float(sp.get("sem") or 0) >= 0.5):
                continue
            # Schwacher Anker (< 0,62, z. B. „Yuri! Wo bist du?“ ↔ „Orpheus, bist du da?“) trägt nur als Fortsetzung des
            # laufenden Takes — nie als Grund, in einen anderen Take zu springen
            if b.art == "dialog" and sp.get("anker") and float(sp.get("anker_score") or 0) < WECHSEL_MIN_SCORE \
                    and not (aktuell is not None and tk.id == aktuell.id) and float(sp.get("sem") or 0) < 0.5:
                continue
            if b.art != "dialog" and not (sp.get("vqa") or sp.get("eroeffnung")):
                continue
            if hoechster_teil and teil(tk) and teil(tk) < hoechster_teil:
                zurueck.append(tk.einstellung or "?")
                continue
            sc = 0.0; warum: list[str] = []
            if sp.get("anker"):
                sc += 2.0 + 0.5 * min(1.0, float(sp.get("staerke") or 0) / 6.0); warum.append("Anker")
            elif sp.get("weich"):
                sc += 1.2; warum.append("mehrdeutige Skriptzeile (weicher Anker)")
            elif float(sp.get("sem") or 0) >= 0.5:
                sc += 1.0; warum.append(f"semantisch {sp['sem']}")
            if sp.get("vqa"):
                sc += 0.6 + (min(1.5, float(sp.get("staerke") or 0)) if b.art in ("schluss", "aktion") else 0.0); warum.append("Bild-Beleg")
            dauer_sp = float(sp["end"]) - float(sp["start"])
            if b.art != "dialog" and dauer_sp < 2.5:
                sc -= 1.0
            if b.art == "dialog" and dauer_sp < 4.0:
                sc -= 0.6; warum.append("sehr kurz")
            if b.art == "eroeffnung" and sp.get("eroeffnung"):
                sc += 0.5; warum.append("Spiel vor der ersten Zeile")
                naechster = takt.get(tk.id, {}).get(b.nr + 1)
                if naechster and naechster.get("anker") and float(naechster.get("anker_score") or 0) >= WECHSEL_MIN_SCORE:
                    sc += 0.8; warum.append("geht nahtlos in den nächsten Beat über")
            if tk.id in beste_ids:
                sc += 0.5
            else:
                sc -= 0.3; warum.append("nicht der beste Take seiner Einstellung")
            if (tk.ng or {}).get("abbruch"):
                sc -= 0.8; warum.append("Abbruch im Take")
            if aktuell is not None and tk.id == aktuell.id:
                if seit_start is not None and offen is not None and (offen.out_s - seit_start) > max_segment:
                    warum.append(f"schon > {max_segment:.0f} s auf dieser Einstellung")
                else:
                    sc += 0.7; warum.append("Kontinuität (gleicher Take)")
            elif aktuell is not None and tk.einstellung == aktuell.einstellung:
                # anderer Take DERSELBEN Einstellung = Jump-Cut — nur wenn der laufende Take den Beat nicht hat
                sc -= 1.0; warum.append("anderer Take derselben Einstellung (Jump-Cut)")
            elif aktuell is not None:
                if seit_start is not None and offen is not None and (offen.out_s - seit_start) > max_segment:
                    sc += 0.6; warum.append("Rhythmus-Wechsel")
                if letzte_figur is not None and b.figur and b.figur != letzte_figur:
                    sc += 0.4; warum.append(f"Sprecherwechsel {letzte_figur} → {b.figur}")
            e_ = tk.einstellung or "?"
            if b.art == "dialog" and sp.get("anker") and dediziert(e_) and (rang(tk) >= 2 or dauer_sp >= 4.0) \
                    and (aktuell is None or not dediziert(aktuell.einstellung or "?")):
                sc += 0.8; warum.append(f"dedizierte Einstellung {e_} für diesen Beat")
            sc += 0.15 * rang(tk)
            if float(sp["end"]) - float(sp["start"]) < 1.5:
                sc -= 1.0
            kand.append((sc, tk, sp, warum))
        if not kand:
            if b.art == "dialog":
                z = next((z for z in sz.zeilen if z.nr == b.dialog_nr), None)
                luecken.append({"szene": sz.nummer, "zeile": b.dialog_nr, "figur": b.figur, "text": z.text if z else b.text,
                                "grund": (f"Beat B{b.nr} nur in früherem Szenenteil ({', '.join(sorted(set(zurueck)))}) belegt — kein Rücksprung hinter {'.'.join(map(str, hoechster_teil))}"
                                          if zurueck else f"Beat B{b.nr} in keinem Take belegt (keine alignierte Zeile, keine eindeutige Improvisation)")})
            else:
                protokoll.append(f"B{b.nr} ({b.art}) ohne Bild-Beleg in einem Take — ausgelassen")
            continue
        kand.sort(key=lambda x: (-x[0], -(x[1].abdeckung or 0)))
        wahl_k = None
        for sc_, tk_, sp_, warum_ in kand:
            if aktuell is not None and tk_.id != aktuell.id:
                texte_ = _phrasen(tk_, float(sp_["start"]), float(sp_["end"]), ausser_zeilen=set(b.zeilen))
                frac, dopp = _schon_gehoert(texte_, tk_.id)
                if len(texte_) >= 2 and frac >= 0.5:
                    protokoll.append(f"B{b.nr}: {clips[tk_.clip_id].dateiname.rsplit('.', 1)[0]} übersprungen — "
                                     f"{int(frac * 100)} % der Sätze schon zu hören (z. B. „{dopp[0][0][:45]}“)")
                    continue
            wahl_k = (sc_, tk_, sp_, warum_)
            break
        if wahl_k is None:
            z = next((z for z in sz.zeilen if z.nr == b.dialog_nr), None) if b.dialog_nr is not None else None
            luecken.append({"szene": sz.nummer, "zeile": b.dialog_nr, "figur": b.figur, "text": (z.text if z else b.text),
                            "grund": f"Beat B{b.nr} entfällt: Inhalt bereits in einem früheren Segment zu hören (gleiche Aufführung, anderer Winkel)"})
            continue
        sc, tk, sp, warum = wahl_k
        clip = clips[tk.clip_id]
        t_in, t_out = float(sp["start"]), float(sp["end"])
        kern_a, kern_b = float(sp["kern"][0]), float(sp["kern"][1])
        # Obergrenzen: Dialog-Beat hält Anker, kappt erst Nachlauf, dann Vorlauf, dann das Ende; Bild-Beat um den Kern
        if b.art == "dialog":
            # Aktions-Vorgriff (Fenster greift vor die Anker-Zeile zurück, z. B. Tee-Servieren vor Z4): der Rückgriff
            # ist gewollter Inhalt — Cap entsprechend erweitern und den Einstieg nicht wieder wegkürzen.
            vorgriff = float(sp.get("vorgriff") or 0.0)
            cap = BEAT_CAP_DIALOG[fein] + vorgriff
            if vorgriff > 0:
                kern_a = min(kern_a, t_in)
            a_sp = sp.get("anker_span") or [kern_a, kern_b]
            if vorgriff > 0:
                a_sp = [min(float(a_sp[0]), t_in + 1.0), float(a_sp[1])]
            if t_out - t_in > cap:
                t_out = min(t_out, kern_b + 1.5)
            if t_out - t_in > cap:
                t_in = max(t_in, kern_a - 1.0)
            if t_out - t_in > cap:
                # Anker bleiben: erst vorn kürzen (bis zur ersten alignierten Zeile), dann hinten
                t_in = max(t_in, min(float(a_sp[0]) - 1.0, t_out - cap))
            if t_out - t_in > cap:
                t_out = max(float(a_sp[1]) + 1.0, t_in + cap) if float(a_sp[1]) + 1.0 - t_in <= cap * 1.3 else t_in + cap
        else:
            cap = BEAT_CAP_AKTION[fein]
            if sp.get("eroeffnung") and sp.get("sprache"):
                cap = float(parameter.get("stumm_max_s", STUMM_MAX))
            if t_out - t_in > cap:
                if sp.get("eroeffnung"):
                    t_out = t_in + cap
                else:
                    m = (max(t_in, kern_a) + min(t_out, kern_b)) / 2
                    t_in, t_out = max(t_in, m - cap / 2), min(t_out, m + cap / 2)
        beleg_beat = [f"B{b.nr} ← {clip.dateiname.rsplit('.', 1)[0]} {t_in:.0f}–{t_out:.0f}s ({', '.join(warum)})"] + list(sp.get("belege") or [])[:3]
        alternativen = [f"{clips[k[1].clip_id].dateiname.rsplit('.', 1)[0]} {float(k[2]['start']):.0f}–{float(k[2]['end']):.0f}s ({k[0]:.1f})" for k in kand[1:3]]
        if alternativen:
            beleg_beat.append("Alternativen: " + " · ".join(alternativen))
        # Verschmelzen mit dem laufenden Segment desselben Takes
        if offen is not None and aktuell is not None and tk.id == aktuell.id:
            luecke = t_in - offen.out_s
            erlaubt = LUECKE_MAX
            if b.aktionen and luecke > LUECKE_MAX and _bewegung_im_fenster(clip, offen.out_s, t_in):
                erlaubt = AKTION_LUECKE_MAX
            if luecke <= erlaubt:
                if luecke > LUECKE_MAX:
                    offen.beleg.append(f"Handlung laut Skript zwischen B{offen.beats[-1]} und B{b.nr} behalten ({luecke:.0f} s Bewegung)")
                offen.out_s = max(offen.out_s, t_out)
                offen.beats.append(b.nr)
                if b.dialog_nr is not None:
                    offen.zeilen.append(b.dialog_nr)
                offen.beleg.extend(beleg_beat)
                if b.art == "dialog":
                    offen.art = "dialog"
                letzte_figur = b.figur or letzte_figur
                if b.art != "dialog" or sp.get("vqa"):
                    gedeckte_aktionen.update(b.aktionen)
                for t_ in _phrasen(tk, offen.in_s, offen.out_s):
                    gehoert.append((t_, tk.id))
                continue
        nr += 1
        fr = framing_je_clip.get(tk.clip_id)
        grund = (f"Beat B{b.nr} ({'Eröffnung' if b.art == 'eroeffnung' else 'Schluss' if b.art == 'schluss' else 'Dialog' if b.art == 'dialog' else 'Handlung'})"
                 f" aus Einstellung {tk.einstellung} T{tk.slate_take}" + (f" [{fr}]" if fr else "") + " — " + ", ".join(warum[:3]))
        e = Eintrag(nr, sz.nummer, str(tk.clip_id), clip.dateiname, tk.einstellung, tk.slate_take, t_in, t_out,
                    [b.dialog_nr] if b.dialog_nr is not None else [], "dialog" if b.art == "dialog" else "stumm", grund, beleg_beat)
        e.beats = [b.nr]
        e.eroeffnung = bool(sp.get("eroeffnung"))
        segmente.append(((b.nr, 0, ()), e))
        offen = e; aktuell = tk; seit_start = t_in
        for t_ in _phrasen(tk, t_in, t_out):
            gehoert.append((t_, tk.id))
        letzte_figur = b.figur or letzte_figur
        if teil(tk) and b.art == "dialog" and sp.get("anker"):
            hoechster_teil = max(hoechster_teil, teil(tk)) if hoechster_teil else teil(tk)
        if b.art != "dialog" or sp.get("vqa"):
            gedeckte_aktionen.update(b.aktionen)

    # ── Phasen-Schnittstelle beim Take-Wechsel: die Intensität darf über eine Coupe INNERHALB der Szene nie
    # zurückfallen. Endet ein Segment mit ambivalenten Wiederholungen/Rufen (weiche Anker < 0,66, „Hallo?/Hey!“) —
    # also dem physischen BEGINN des nächsten Beats — und kommt der nächste Beat aus einem ANDEREN Take, wird das
    # Segment nach seiner letzten HARTEN Anker-Zeile (+3 s Nachlauf) beendet: die Eskalation spielt der nächste
    # Take in der richtigen Intensität. (Nutzer-Befund 20.08.: Nr9 endete stehend über Yuri @69 s, Nr10 begann
    # wieder sitzend @80 s.) Gleicher Take → kein Schnitt, die Eskalation ist dort natürlich kontinuierlich.
    HART = 0.66
    zeilen_von_beats = {b_.nr: set(b_.zeilen) for b_ in beats}
    tk_von_clip = {str(t.clip_id): t for t in tks}
    master_segs = [e_ for _, e_ in segmente]
    for e_, e_next in zip(master_segs, master_segs[1:]):
        if e_.art != "dialog" or e_next.clip_id == e_.clip_id or not e_.beats:
            continue
        tk_ = tk_von_clip.get(e_.clip_id)
        if tk_ is None:
            continue
        eigene_zeilen = set().union(*(zeilen_von_beats.get(b_, set()) for b_ in e_.beats))
        harte = sorted((it for it in (tk_.zeilen or [])
                        if it.get("art") == "spiel" and it.get("skript_zeile_nr") in eigene_zeilen
                        and float(it.get("score") or 0) >= HART
                        and e_.in_s - 0.2 <= float(it["start"]) and float(it["end"]) <= e_.out_s + 0.2),
                       key=lambda it: float(it["start"]))
        if not harte:
            continue
        # Anker-Kette: eine harte Zeile mit NEUER Skript-Zeile verlängert die Kette immer (Dialog-Progression —
        # Sz3: Z7 → 24 s Handlung → Z9; Sz5-Tirade: Z3 … Z8, Z9, Z10). Eine harte WIEDERHOLUNG einer schon
        # gekesteten Zeile nach > 12 s Lücke ist Eskalation („hörst du mich?!“ = viertes Z4 nach 43 s Gerufe)
        # und verlängert NICHT — genau der Schwanz, den der nächste Take in richtiger Intensität übernimmt.
        gekettet: set = set()
        letzte = None
        for it in harte:
            znr = it.get("skript_zeile_nr")
            if letzte is None or znr not in gekettet or float(it["start"]) - letzte <= 12.0:
                letzte = float(it["end"]) if letzte is None else max(letzte, float(it["end"]))
                gekettet.add(znr)
        if letzte is None:
            continue
        cut = min(e_.out_s, letzte + 3.0)
        if e_.out_s - cut >= 4.0 and cut - e_.in_s >= 4.0:
            alt_out = e_.out_s
            e_.out_s = round(cut, 2)
            e_.beleg.append(f"Phasen-Schnitt: Ende bei {cut:.1f}s statt {alt_out:.1f}s — ambivalente Wiederholungen/"
                            f"Rufe danach gehören dem nächsten Beat (Take-Wechsel, Intensität darf nicht zurückfallen)")

    # ── Anschluss-Auslauf beim Take-Wechsel (Nutzer-Regel 20.08., „Match auf die Bewegung“): enthält der SCHEIDENDE
    # Take auch den ersten Beat des nächsten Segments (seine Anker-Zeile existiert dort später), läuft das Segment
    # durch die STUMME Anschluss-Handlung (Aufstehen, Gang zur Tür) weiter — bis kurz vor die eigene Anker-Zeile;
    # die Replik übernimmt dann der neue Take. Nur reine Aktions-Brücken (keine alignierten Zeilen darin — sonst gilt
    # der Phasen-Schnitt), nur mit echtem Bewegungs-Moment, max. 20 s. (Befund Sz3: Aufstehen+Gang 110–118 in 3.1/T4
    # fehlten vor dem Schnitt auf 3.2/T3 an der Tür.)
    for e_, e_next in zip(master_segs, master_segs[1:]):
        if e_.art not in ("dialog", "stumm") or e_next.clip_id == e_.clip_id or not e_next.beats:
            continue
        tk_ = tk_von_clip.get(e_.clip_id)
        c_ = clips.get(uuid.UUID(e_.clip_id)) if tk_ is not None else None
        if tk_ is None or c_ is None:
            continue
        zeilen_next = zeilen_von_beats.get(min(e_next.beats), set())
        anker_start = min((float(it["start"]) for it in (tk_.zeilen or [])
                           if it.get("art") == "spiel" and it.get("skript_zeile_nr") in zeilen_next
                           and float(it["start"]) > e_.out_s + 0.5), default=None)
        if anker_start is None:
            continue
        bruecke_ende = anker_start - 0.5
        if not (1.5 <= bruecke_ende - e_.out_s <= 20.0):
            continue
        if any(it.get("art") == "spiel" and it.get("skript_zeile_id")
               and e_.out_s + 0.2 < float(it["start"]) < bruecke_ende for it in (tk_.zeilen or [])):
            continue          # Brücke enthält Repliken → Phasen-Schnitt-Domäne, kein Auslauf
        proxy = PROXY_DIR / f"{clip_stem(c_)}_proxy.mp4"
        if not (proxy.exists() and proxy.stat().st_size > 0):
            continue
        k = AK.kurve(str(proxy))
        region = [(t, v) for t, v in k if e_.out_s <= t <= bruecke_ende]
        if len(region) < 3:
            continue
        alle_v = sorted(v for _, v in k)
        schwelle = max(1.5, 2.0 * (alle_v[len(alle_v) // 2] if alle_v else 0.0))
        bewegte = [t for t, v in region if v >= schwelle]
        if not bewegte:
            continue          # keine Anschluss-Bewegung → harter Schnitt bleibt
        neu_out = round(min(bruecke_ende, max(bewegte) + 0.8), 2)
        if neu_out - e_.out_s >= 1.5:
            alt_out = e_.out_s
            e_.out_s = neu_out
            e_.beleg.append(f"Anschluss-Auslauf: bis {neu_out:.1f}s statt {alt_out:.1f}s — stumme Anschluss-Handlung "
                            f"vor dem Take-Wechsel (Match auf die Bewegung; die Zeile übernimmt {e_next.einstellung} T{e_next.take})")

    # ── Schluss-Auslauf (Nutzer-Regel 20.08.): das LETZTE Master-Segment der Szene läuft bis zum Ende des Spiels —
    # die stille Handlung nach der letzten Zeile (Weinen, Umarmen = der Skript-Schluss) ist Rohschnitt-Gold und wird
    # NICHT abgeschnitten. Grenzen setzen nur echte Gegen-Signale: Produktions-Sprech, sichtbarer Ausstieg
    # (Bewegungssprung: Aufstehen/Lachen), Clip-Ende. Der Cutter kürzt lieber selbst, als Material zu verlieren.
    letztes = next((e_ for e_ in reversed(master_segs) if e_.art in ("dialog", "stumm") and e_.spur == 1), None)
    schluss_beat = next((b_ for b_ in beats if b_.art == "schluss"), None)
    if letztes is not None:
        tk_ = tk_von_clip.get(letztes.clip_id)
        c_ = clips.get(uuid.UUID(letztes.clip_id)) if tk_ is not None else None
        if tk_ is not None and c_ is not None:
            # Ziel = Clip-Ende (NICHT das letzte gesprochene Wort — die stumme Handlung danach ist der Punkt),
            # begrenzt durch Produktions-/Slate-Sprech nach dem aktuellen Ende und den sichtbaren Ausstieg IM Auslauf
            # (Baseline = Anfang des Auslaufs selbst: Weinen ist dort die Norm, erst der echte Bruch — Aufstehen,
            # Crew — springt darüber hinaus).
            ziel = max(letztes.out_s, float(c_.dauer or letztes.out_s) - 0.5)
            prod = min((float(it["start"]) for it in (tk_.zeilen or [])
                        if it.get("art") in ("produktion", "slate") and float(it["start"]) > letztes.out_s + 0.5),
                       default=None)
            if prod is not None:
                ziel = min(ziel, prod - 0.3)
            grenz_signal = None
            # Wächter 2 — Bewegungs-Bruch: Schwelle relativ zur Lamentations-Baseline (Median der ersten Hälfte des
            # Auslaufs, ×2,5, Boden 4 %). Der alte 3×-Kern-Schwellwert übersah das Aufstehen/die Crew (9,1 % @151 s).
            proxy = PROXY_DIR / f"{clip_stem(c_)}_proxy.mp4"
            if proxy.exists() and proxy.stat().st_size > 0 and ziel - letztes.out_s >= 3.0:
                k = AK.kurve(str(proxy))
                region = [(t, v) for t, v in k if letztes.out_s <= t <= ziel]
                if len(region) >= 8:
                    basis = sorted(v for _, v in region[:max(4, len(region) // 2)])
                    schwelle = max(4.0, 2.5 * basis[len(basis) // 2] + 1.0)
                    for t, v in region:
                        if t > letztes.out_s + 3.0 and v >= schwelle:
                            ziel = min(ziel, t - 0.5)
                            grenz_signal = f"Bewegungs-Bruch bei {t:.0f}s ({v:.0f}% ≥ {schwelle:.0f}%)"
                            break
            # Wächter 3 — Wiederauferstehung/Crew: taucht im Auslauf ein Gesicht nach ≥ 10 s Abwesenheit wieder auf
            # (die „Tote“ hebt den Kopf, jemand tritt ins Bild), ist das Spiel vorbei — Ende davor.
            # (Befund T007: Ophelia+Yuri-Spans enden 130/135, neue Spans ab 145 = Aufwachen + Crew.)
            for spans in (v.get("spans") or [] for v in (tk_.gesichter or {}).values()):
                for vor, nach in zip(spans, spans[1:]):
                    a_neu = float(nach[0])
                    # Nur Brüche, deren ABWESENHEIT im Segment selbst beginnt (Kopf gesenkt → wieder hoch = Aufwachen);
                    # ein Wiederauftauchen nach einer Lücke, die schon VOR dem Segment begann, ist bloß Kadrage.
                    if (letztes.out_s + 3.0 < a_neu <= ziel and a_neu - float(vor[1]) >= 10.0
                            and float(vor[1]) >= letztes.in_s):
                        ziel = min(ziel, a_neu - 0.5)
                        grenz_signal = f"Gesicht nach {a_neu - float(vor[1]):.0f}s Pause wieder erkannt bei {a_neu:.0f}s (Wiederauferstehung/Crew)"
            if ziel - letztes.out_s >= 3.0:
                alt_out = letztes.out_s
                letztes.out_s = round(ziel, 2)
                grund_txt = f" (Skript-Schluss: „{schluss_beat.text[:50]}“)" if schluss_beat is not None else ""
                letztes.beleg.append(f"Schluss-Auslauf: bis {ziel:.1f}s statt {alt_out:.1f}s — stille Handlung nach der "
                                     f"letzten Zeile bleibt im Rough Master{grund_txt}"
                                     + (f" · Ende durch {grenz_signal}" if grenz_signal else ""))
                if schluss_beat is not None:
                    gedeckte_aktionen.update(schluss_beat.aktionen)
                    protokoll[:] = [p_ for p_ in protokoll if not p_.startswith(f"B{schluss_beat.nr} ")]
                    protokoll.append(f"B{schluss_beat.nr} (schluss) durch Schluss-Auslauf des letzten Master-Segments gedeckt")
    return segmente, nr, gedeckte_aktionen, protokoll


def geschwister_fuer_plan(eintraege: list["Eintrag"], takes_je_szene: dict, clips: dict, sk: Skript,
                          max_pro_szene: int = 2) -> list["Eintrag"]:
    """Geschwister-Spuren (Nutzer-Logik 20.08. v5 — ELASTISCH): je Szene die besten Takes der
    NICHT-Master-Einstellungen auf Spur 2/3, aber nicht als EIN Block — zwei Takes haben nie
    dasselbe Tempo und selten dieselben Worte. Stattdessen wird das Geschwister an JEDEM Beat neu
    synchronisiert: seine takt-Spans liefern Sync-Punkte (Beat-Zeit im Geschwister ↔ Moment des
    Beats im Master auf der Timeline); zwischen zwei Sync-Punkten läuft der Block durch und wird
    gerognet, wenn das Geschwister langsamer spielt als der Master. Drift ist damit auf eine
    Beat-Länge begrenzt — Multicam-Logik statt globalem Offset."""
    from backend.core.skript.beats import _spiel_grenzen
    szenen_by_nr = {sz.nummer: sz for sz in sk.szenen}
    master_je_szene: dict[str, list[Eintrag]] = defaultdict(list)
    for e in eintraege:
        if e.spur == 1 and e.art in ("dialog", "stumm") and e.tl_start is not None:
            master_je_szene[e.szene].append(e)
    out: list[Eintrag] = []
    for sz_nr, master_segs in master_je_szene.items():
        sz = szenen_by_nr.get(sz_nr)
        if sz is None:
            continue
        tks = takes_je_szene.get(sz.id, [])
        master_einst = {e.einstellung for e in master_segs}
        master_tk = {e.einstellung: next((t for t in tks if str(t.clip_id) == e.clip_id), None) for e in master_segs}
        szene_tl_start = min(e.tl_start for e in master_segs)
        szene_tl_ende = max(e.tl_start + (e.out_s - e.in_s) for e in master_segs)
        # Beat → Timeline-Moment im Master (belegte Spans direkt, Lücken interpoliert am Ende des
        # letzten vorherigen Spans; +2 Beats über die Plage hinaus für Nachzügler wie 5.2-T4s B12)
        m_pos: dict[int, float] = {}
        for e in sorted(master_segs, key=lambda x: x.tl_start):
            mt = master_tk.get(e.einstellung)
            m_all = {int(x["beat"]): x for x in ((mt.takt if mt else None) or [])}
            if not e.beats:
                continue
            for b in range(min(e.beats), max(e.beats) + 3):
                if b in m_pos:
                    continue
                if b in m_all:
                    pos = float(m_all[b]["kern"][0])
                else:
                    vorher = [sp for bb, sp in m_all.items() if bb < b]
                    if not vorher:
                        continue
                    pos = float(max(vorher, key=lambda sp: float(sp["kern"][1]))["kern"][1])
                pos = min(max(pos, e.in_s), e.out_s)
                m_pos[b] = e.tl_start + (pos - e.in_s)
        # bester Take je Nicht-Master-Einstellung (Anker-Beats > Span-Anzahl > Spielfenster)
        beste: dict[str, TakeKontext] = {}
        for t in tks:
            e_key = t.einstellung or "?"
            if e_key in master_einst or not t.takt or t.clip_id not in clips:
                continue
            def guete(t_):
                sp = [x for x in (t_.takt or []) if x.get("evidenz")]
                return (sum(1 for x in sp if x.get("anker")), len(sp),
                        float(t_.spiel_ende_s or 0) - float(t_.spiel_start_s or 0))
            if e_key not in beste or guete(t) > guete(beste[e_key]):
                beste[e_key] = t
        rangfolge = sorted(beste.values(), key=lambda t_: (
            -sum(1 for x in (t_.takt or []) if x.get("evidenz") and x.get("anker")),
            -len([x for x in (t_.takt or []) if x.get("evidenz")])))[:max_pro_szene]
        for lane_i, t in enumerate(rangfolge):
            c = clips[t.clip_id]
            try:
                s0, s1 = _spiel_grenzen(t, c)
            except Exception:  # noqa: BLE001
                s0, s1 = float(t.spiel_start_s or 0.0), float(t.spiel_ende_s or (c.dauer or 0.0))
            # Sync-Punkte: (Beat, Zeit im Geschwister, Ziel-Moment) — streng monoton in beiden Achsen
            sync: list[tuple[int, float, float]] = []
            for x in sorted((x for x in (t.takt or []) if x.get("evidenz")), key=lambda x: int(x["beat"])):
                b = int(x["beat"])
                if b not in m_pos:
                    continue
                ts = max(s0, float(x["kern"][0]))
                tl_b = m_pos[b]
                if sync and (ts <= sync[-1][1] + 0.5 or tl_b <= sync[-1][2] + 0.5):
                    continue
                if ts >= s1 - 1.0:
                    continue
                sync.append((b, ts, tl_b))
            if not sync:
                continue   # kein gemeinsamer Moment — ehrlich weglassen statt falsch stapeln
            # Vorlauf: der erste Block darf etwas VOR seinem Sync-Punkt beginnen (Atem), soweit
            # Geschwister und Szene Platz haben
            vor = min(sync[0][1] - s0, sync[0][2] - szene_tl_start, 3.0)
            vor = max(0.0, vor)
            for i, (b, ts, tl_b) in enumerate(sync):
                block_in = ts - (vor if i == 0 else 0.0)
                block_tl = tl_b - (vor if i == 0 else 0.0)
                ende_s = sync[i + 1][1] if i + 1 < len(sync) else s1
                ende_tl = sync[i + 1][2] if i + 1 < len(sync) else szene_tl_ende
                dauer = min(ende_s - block_in, ende_tl - block_tl, s1 - block_in)
                if dauer < 1.5:
                    continue
                g = Eintrag(0, sz_nr, str(t.clip_id), c.dateiname, t.einstellung, t.slate_take,
                            round(block_in, 2), round(block_in + dauer, 2), [], "alternative",
                            f"Geschwister B{b}: Einstellung {t.einstellung} T{t.slate_take} — "
                            f"am Master-Moment dieses Beats ausgerichtet (elastische Beat-Sync)",
                            [f"Sync-Punkt B{b}: Geschwister {ts:.1f} s ↔ Timeline {tl_b:.1f} s"
                             + (f", Vorlauf {vor:.1f} s" if i == 0 and vor > 0 else "")])
                g.video_only = True
                g.spur = 2 + lane_i
                g.beats = [b]
                g.tl_start = round(block_tl, 3)
                out.append(g)
    return out


def alternativen_fuer_plan(eintraege: list["Eintrag"], takes_je_szene: dict, clips: dict, sk: Skript,
                           max_pro_beat: int = 2, nur_szene: str | None = None, nur_beat: int | None = None,
                           basis_spur: int = 3, ausschluss_einst: set | None = None) -> list["Eintrag"]:
    """Alternativen-Stapel: für jeden Beat der Master-Segmente (Spur 1, mit `beats`) die besten Spans ANDERER Takes,
    stumm auf Spur 2..(1+max_pro_beat), am Beat-Anfang des Masters ausgerichtet. Rangfolge: Anker > Anker-Score >
    Stärke. Wird vom Feinschnitt-Generator (automatisch) UND vom Agent-Tool (auf Zuruf) benutzt."""
    szenen_by_nr = {sz.nummer: sz for sz in sk.szenen}
    out: list[Eintrag] = []
    for e in eintraege:
        if e.art not in ("dialog", "stumm") or not e.beats or e.spur != 1 or e.tl_start is None:
            continue
        if nur_szene is not None and e.szene != nur_szene:
            continue
        sz = szenen_by_nr.get(e.szene)
        if sz is None:
            continue
        tks = takes_je_szene.get(sz.id, [])
        master_tk = next((t for t in tks if str(t.clip_id) == e.clip_id), None)
        master_takt = {int(sp["beat"]): sp for sp in ((master_tk.takt if master_tk else None) or [])}
        for b in e.beats:
            if nur_beat is not None and b != nur_beat:
                continue
            # Position des Beats im Master-Segment (Kern-Anfang, ins Segment geklemmt).
            # Fehlt der Span (innen gedeckter Beat ohne Evidenz im Master): am Ende des letzten
            # VORHERIGEN Spans interpolieren — nie an den Segment-Anfang stapeln.
            m_sp = master_takt.get(b)
            if m_sp is not None:
                beat_in = min(max(float(m_sp["kern"][0]), e.in_s), e.out_s - 1.0)
            else:
                vorher = [sp for bb, sp in master_takt.items() if bb < b]
                if not vorher and e.beats and b != min(e.beats):
                    continue
                beat_in = min(max(float(max(vorher, key=lambda sp: float(sp["kern"][1]))["kern"][1]) if vorher else e.in_s,
                                  e.in_s), e.out_s - 1.0)
            tl_at = (e.tl_start or 0.0) + (beat_in - e.in_s)
            rest = e.out_s - beat_in
            kand = []
            for t in tks:
                if str(t.clip_id) == e.clip_id or t.clip_id not in clips:
                    continue
                if ausschluss_einst and (e.szene, t.einstellung or "?") in ausschluss_einst:
                    continue   # Einstellung liegt schon als GANZE Geschwister-Spur auf V2/V3
                sp = next((x for x in (t.takt or []) if int(x.get("beat", -1)) == b and x.get("evidenz")), None)
                if sp is None:
                    continue
                rang = (1 if sp.get("anker") else 0, float(sp.get("anker_score") or 0), float(sp.get("staerke") or 0))
                kand.append((rang, t, sp))
            kand.sort(key=lambda x: x[0], reverse=True)
            for rang, t, sp in kand[:max_pro_beat]:
                c = clips[t.clip_id]
                a = max(float(sp["kern"][0]) - 0.5, float(sp["start"]))
                dauer = min(float(sp["kern"][1]) + 0.8, float(sp["end"])) - a
                dauer = max(2.0, min(dauer, 12.0, rest))
                if dauer < 2.0:
                    continue
                alt = Eintrag(0, e.szene, str(t.clip_id), c.dateiname, t.einstellung, t.slate_take,
                              round(a, 2), round(a + dauer, 2), [], "alternative",
                              f"Alternative für B{b} aus {t.einstellung} T{t.slate_take}"
                              + (" (Anker)" if sp.get("anker") else "") + f" — parallel zu Segment Nr{e.nr}",
                              list(sp.get("belege") or [])[:2])
                alt.beats = [b]
                alt.video_only = True
                alt.tl_start = round(tl_at, 3)
                out.append(alt)
    # ── Spur-Zuteilung OHNE Überlappung: eine Alternative kommt nur auf eine Spur, die an ihrer Position frei ist
    # (aufeinanderfolgende Beats haben oft überlappende Fenster). Keine freie Spur → Alternative entfällt.
    # Ohne das persistierte der Plan überlappende Clips auf V2 → das Timeline-Modell (Editor) verweigert → Schwarzbild.
    # Spur-Zuteilung: (a) überlappungsfrei, (b) DRAMATURGISCH KONSISTENT — je Szene bekommt jede EINSTELLUNG ihre
    # feste Spur (V3 = ein Blickwinkel, V4 = der andere). Eine Alternativ-Spur einzublenden heißt dann: EIN konstanter
    # Kamerawinkel, kein Links/Rechts-Zapping (Nutzer-Regel 20.08.). Einstellungen über das Lane-Budget hinaus entfallen.
    out.sort(key=lambda a: (a.tl_start or 0.0))
    # Lane-Vergabe je Szene nach WICHTIGKEIT der Einstellung (Anker-Alternativen > Anzahl), nicht nach Zufall der
    # Timeline-Reihenfolge — sonst schnappt ein einzelner 7-s-Clip (2.4) der Anker-Einstellung (2.2) die Spur weg.
    gewicht: dict[tuple, list] = {}
    for alt in out:
        k_ = (alt.szene, alt.einstellung or "?")
        g = gewicht.setdefault(k_, [0, 0])
        g[0] += 1 if "(Anker)" in alt.grund else 0
        g[1] += 1
    lane_je_einst: dict[tuple, int] = {}
    for szene in {k_[0] for k_ in gewicht}:
        einst_sortiert = sorted((k_ for k_ in gewicht if k_[0] == szene),
                                key=lambda k_: (-gewicht[k_][0], -gewicht[k_][1], _einst_key(k_[1])))
        for i, k_ in enumerate(einst_sortiert[:max_pro_beat]):
            lane_je_einst[k_] = basis_spur + i
    spur_ende: dict[int, float] = {}
    platziert: list[Eintrag] = []
    for alt in out:
        spur = lane_je_einst.get((alt.szene, alt.einstellung or "?"))
        if spur is None:
            continue          # Einstellung über dem Lane-Budget — Konsistenz vor Menge
        t0 = alt.tl_start or 0.0
        if spur_ende.get(spur, 0.0) <= t0 + 0.05:
            alt.spur = spur
            spur_ende[spur] = t0 + (alt.out_s - alt.in_s)
            platziert.append(alt)
    return platziert


def _plane_szene_master(sz: SkriptSzene, tks: list[TakeKontext], clips: dict, framing_je_clip: dict,
                        parameter: dict, nr: int) -> tuple[list[tuple[tuple, "Eintrag"]], int, list[str]]:
    """Szenen-Master (Nutzer-Logik 20.08. v2): V1 erzählt die Szene mit der MINIMALEN Take-Kette —
    meist genau EIN Take —, der von der Anspiel-Barriere bis zum Spielende durchläuft, ohne interne
    Schnitte und ohne Take-Mosaik. Die Beat-/Coverage-Intelligenz wandert auf die Spuren darüber
    (`alternativen_fuer_plan` richtet sich an den takt-Spans des Master-Takes aus).

    Take-Wahl je Szene: Beat-Abdeckung (wie viel Geschichte der Take belegt trägt, anker-gewichtet)
    + Vollständigkeit des Spielfensters + Bewegungs-Dynamik (Anteil überdurchschnittlicher Aktivität)
    + Framing-Bonus (weite/mittlere Einstellungen tragen eine Szene). Deckt kein einzelner Take alle
    Beats (getrennt gedrehte Szenen-Teile, z. B. 5.1.x/5.2.x), ergänzt ein Greedy-Set-Cover die
    minimale Kette; jedes Glied läuft bis zu SEINEM Spielende."""
    from backend.core.skript.beats import beats_fuer_szene, _spiel_grenzen
    beats = beats_fuer_szene(sz)
    protokoll: list[str] = []
    if not beats:
        return [], nr, protokoll
    dialog_beats = {b.nr for b in beats if b.art == "dialog"}
    dialog_nr_je_beat = {b.nr: b.dialog_nr for b in beats if b.dialog_nr is not None}

    # ── Heimat-Teil je Beat (nur dreistufige Klappe, z. B. 5.1.x / 5.2.x): getrennt gedrehte
    # Szenen-TEILE. Ein Take darf für die Kette nur Beats seines eigenen Teils beanspruchen —
    # sonst „deckt“ der weite 5.1-Master die Konfrontation aus 5.2 mit einem fernen 17-s-Echo
    # und der Film endet vor dem Schluss (Nutzer-Befund v20, Szene 5). Heimat = Teil des Takes
    # mit der stärksten harten Anker-Evidenz; Beats ohne harten Anker erben die Heimat des
    # vorherigen Beats.
    dreistufig = any(len(_einst_key(t.einstellung)) >= 3 for t in tks)
    heimat_teil: dict[int, tuple] = {}
    if dreistufig:
        bester_anker: dict[int, tuple[float, tuple]] = {}
        for t in tks:
            t_teil = _einst_key(t.einstellung)[:2]
            for sp in (t.takt or []):
                sc = float(sp.get("anker_score") or 0.0)
                b_ = int(sp["beat"])
                if sp.get("evidenz") and sp.get("anker") and sc >= WECHSEL_MIN_SCORE \
                        and sc > bester_anker.get(b_, (0.0, ()))[0]:
                    bester_anker[b_] = (sc, t_teil)
        letzte_heimat: tuple = ()
        for b_ in sorted(bb.nr for bb in beats):
            if b_ in bester_anker:
                letzte_heimat = bester_anker[b_][1]
            if letzte_heimat:
                heimat_teil[b_] = letzte_heimat

    kandidaten = []   # (score, tk, clip, ansprueche, alle_beats, s0, s1, score_text)
    for tk in tks:
        c = clips.get(tk.clip_id)
        if c is None or not tk.takt:
            continue
        tk_teil = _einst_key(tk.einstellung)[:2]
        spans = {}
        for sp in tk.takt:
            if not sp.get("evidenz"):
                continue
            b_ = int(sp["beat"])
            # Ketten-Anspruch: Dialog-Beats nur mit HARTEM Anker (≥ WECHSEL_MIN_SCORE) —
            # weiche Echos zählen für die Story-Deckung nicht; Eröffnung/Aktion/Schluss per Evidenz.
            if b_ in dialog_beats and not (sp.get("anker") and float(sp.get("anker_score") or 0) >= WECHSEL_MIN_SCORE):
                continue
            # Teil-Regel: fremder Szenen-Teil ist nie Anspruch dieses Takes
            if heimat_teil.get(b_) and heimat_teil[b_] != tk_teil:
                continue
            spans[b_] = sp
        if not spans:
            continue
        alle_beats = sorted({int(sp["beat"]) for sp in tk.takt})
        deck = sum(1.0 + (0.5 if sp.get("anker") else 0.0) + min(1.0, float(sp.get("anker_score") or 0.0))
                   for sp in spans.values())
        try:
            s0, s1 = _spiel_grenzen(tk, c)
        except Exception:  # noqa: BLE001
            s0, s1 = float(tk.spiel_start_s or 0.0), float(tk.spiel_ende_s or (c.dauer or 0.0))
        voll = max(0.0, s1 - s0)
        dyn = 0.0
        proxy = PROXY_DIR / f"{clip_stem(c)}_proxy.mp4"
        if proxy.exists() and proxy.stat().st_size > 0:
            try:
                k = AK.kurve(str(proxy))
                werte = [v for _, v in k] if k else []
                if werte:
                    med = sorted(werte)[len(werte) // 2]
                    dyn = min(1.0, 3.0 * sum(1 for v in werte if v > 2 * max(med, 1e-6)) / max(1, len(werte)))
            except Exception:  # noqa: BLE001
                pass
        fr = FRAMING_RANG.get(framing_je_clip.get(tk.clip_id), 0)
        fr_bonus = {1: 0.6, 2: 0.5}.get(fr, 0.2)
        score = deck + voll / 30.0 + dyn + fr_bonus
        kandidaten.append((score, tk, c, spans, alle_beats, s0, s1,
                           f"Deckung {deck:.1f} · Spiel {voll:.0f}s · Dynamik {dyn:.2f} · Framing +{fr_bonus:.1f}"))
    if not kandidaten:
        protokoll.append("Szenen-Master: kein Take mit Beat-Evidenz — Szene bleibt Lücke")
        return [], nr, protokoll

    # Greedy-Minimal-Kette: erst der Take mit der größten Rest-Abdeckung (Score als Tie-Break).
    # INNEN-REGEL: ein Beat ZWISCHEN min und max der belegten Beats eines Takes gilt als gedeckt —
    # der Take läuft dort physisch durch (Evidenz-Loch ≠ Story-Loch, Befund Szene 2: B2 ohne Beleg
    # in 2.1 T2 hätte sonst die halbe Szene doppelt erzählt). Kette nur für Beats JENSEITS der Plage.
    # GESCHWISTER-REGEL (Nutzer 20.08. v3): die Klappen-Nummerierung IST das Signal — zweistufig
    # (4.1/4.3) = dieselbe Handlung aus anderen Winkeln → genau EIN Take auf V1, die Geschwister-
    # Einstellungen werden Coverage auf V2+; dreistufig (5.1.x/5.2.x) = getrennte Szenen-TEILE →
    # Kette nur ÜBER Teil-Grenzen, ein Take pro Teil.
    rest = {b.nr for b in beats}
    pool = list(kandidaten)
    kette = []
    benutzte_teile: set = set()
    while rest and pool:
        pool.sort(key=lambda x: (-len(set(x[3]) & rest), -x[0]))
        if kette and not dreistufig:
            break   # zweistufige Klappe: ein Take erzählt die Szene, Rest = Geschwister-Coverage
        best = None
        for i, k in enumerate(pool):
            if dreistufig and _einst_key(k[1].einstellung)[:2] in benutzte_teile:
                continue
            if set(k[3]) & rest:
                best = pool.pop(i)
                break
        if best is None:
            break
        neu = set(best[3]) & rest
        kette.append((best, sorted(neu)))
        benutzte_teile.add(_einst_key(best[1].einstellung)[:2])
        rest -= set(range(min(best[3]), max(best[3]) + 1))
    if rest:
        # Schluss-Beats jenseits der letzten belegten Plage laufen im Spielende des letzten Glieds
        # physisch mit (das Segment endet erst am Clip-Ende) — ehrlich unterscheiden von echten Löchern.
        max_belegt = max(max(x[0][3]) for x in kette) if kette else -1
        nachlauf_beats = {b for b in rest if b > max_belegt}
        echte = rest - nachlauf_beats
        if nachlauf_beats:
            protokoll.append("Szenen-Master: " + ", ".join(f"B{b}" for b in sorted(nachlauf_beats))
                             + " ohne eigenen Beleg — läuft im Spielende des letzten Glieds mit")
        if echte:
            protokoll.append("Szenen-Master: Beats ohne Take-Beleg (kein Glied deckt sie): "
                             + ", ".join(f"B{b}" for b in sorted(echte)))
    kette.sort(key=lambda x: min(x[1]))   # Story-Reihenfolge nach dem ersten NEU gedeckten Beat

    # Nach dem Story-Sort wird Einstieg + Verantwortung je Glied SEQUENZIELL bestimmt — auf Basis
    # der PLAGEN der vorherigen Glieder (nicht des Greedy-„neu“): sonst spielt ein Folge-Glied einen
    # Beat nach, den das vorige Glied physisch schon erzählt hat (Befund Szene 4: T5 stieg bei B1
    # ein und wiederholte die Replik, die 4.1 T3 gerade gespielt hatte).
    segmente: list[tuple[tuple, Eintrag]] = []
    bereits: set[int] = set()
    for idx, ((score, tk, c, spans, alle_beats, s0, s1, _stext), _neu) in enumerate(kette):
        plage = set(range(min(spans), max(spans) + 1))   # Anspruchs-Plage (harte Deckung + innen)
        eigene = sorted(plage - bereits)
        if not eigene:
            continue   # Glied vollständig redundant nach Story-Sort
        spans_alle = {int(sp["beat"]): sp for sp in (tk.takt or [])}
        in_s = s0
        leerbild_beleg = None
        if idx == 0:
            # Leerbild-Trim: beginnt der Take nach der Klappe mit LEEREM Bild (niemand da, nichts
            # bewegt sich — vorgespieltes Einrichten/leerer Set), startet die Szene erst kurz vor
            # der ersten PRÄSENZ (frühestes Gesicht ODER Bewegungs-Einsatz) − 3 s Atem. Befund v23,
            # Szene 3: 8 s leerer Raum nach der Szenen-Blende lasen sich wie eine falsche Szene.
            praesenz_kandidaten = []
            gsp = [float(sp[0]) for info in (tk.gesichter or {}).values()
                   for sp in (info.get("spans") or []) if float(sp[0]) >= s0 - 1.0]
            if gsp:
                praesenz_kandidaten.append(min(gsp))
            proxy = PROXY_DIR / f"{clip_stem(c)}_proxy.mp4"
            if proxy.exists() and proxy.stat().st_size > 0:
                try:
                    af = AK.aktives_fenster(AK.kurve(str(proxy)))
                    if af is not None and float(af[0]) >= s0 - 1.0:
                        praesenz_kandidaten.append(float(af[0]))
                except Exception:  # noqa: BLE001
                    pass
            if praesenz_kandidaten:
                praesenz = min(praesenz_kandidaten)
                # Deckel: NIE über den Beginn des ersten Anspruchs-Beats oder die erste gespielte
                # Äußerung hinaus trimmen — Off-Screen-Dialog über „leerem“ Bild ist legitimes Kino
                # (Szene 5: „Orpheus, bist du da?“ hinter der Tür, Gesicht erst bei 50 s).
                erster_inhalt = min([float(sp["start"]) for sp in spans.values()]
                                    + [float(it["start"]) for it in (tk.zeilen or []) if it.get("art") == "spiel"])
                ziel_in = max(s0, min(praesenz - 3.0, erster_inhalt - 1.0))
                if ziel_in > s0 + 1.0:
                    in_s = ziel_in
                    leerbild_beleg = (f"Leerbild-Trim: Präsenz (Gesicht/Bewegung) ab {praesenz:.1f} s, "
                                      f"erster Inhalt {erster_inhalt:.1f} s → Einstieg {in_s:.1f} s statt {s0:.1f} s")
        if idx > 0:
            # Folge-Glied: Einstieg am ersten Beat, den die vorigen Glieder NICHT erzählt haben —
            # nie vor der eigenen Anspiel-Barriere, kein Story-Rücksprung.
            sp = next((spans_alle[b] for b in eigene if b in spans_alle), None)
            if sp is not None:
                in_s = max(s0, float(sp["start"]) - 0.5)
            # Story-Repeat-Wächter: die EIGENE Version bereits erzählter Dialog-Beats überspringen —
            # sonst rembobiniert die Story an der Naht (Befund v22, Szene 4: Glied 2 stieg mitten in
            # seiner „Ey, du Scheißteil!“-Replik ein, die Glied 1 gerade fertig erzählt hatte; der
            # Zuschauer hört die Zeile doppelt, während das Bild schon weiter ist).
            wieder = max((float(spans_alle[b]["end"]) for b in bereits
                          if b in dialog_beats and b in spans_alle), default=None)
            if wieder is not None:
                in_s = max(in_s, wieder + 0.3)
        # NUR das LETZTE Glied läuft bis zum Spielende; frühere Glieder enden nach ihrem letzten
        # Anspruchs-Beat (+3 s Atem) — sonst spielt z. B. der 5.1-Master seinen 40-s-Nachklang,
        # bevor die 5.2-Konfrontation überhaupt beginnt.
        if idx == len(kette) - 1:
            out_s = max(in_s + 1.0, s1)
        else:
            letzter_anspruch = max((float(spans[b]["end"]) for b in spans if b in plage), default=s1)
            # Die Handlung endet nicht mit dem letzten Anspruchs-Beat: gespielte Äußerungen danach
            # (Rufe zum Fernseher, Improvisation) gehören zur Szene — Atem zählt ab der LETZTEN
            # gespielten Äußerung im Fenster (Befund 4.1 T3: „steht auf, geht zum TV, beschwert
            # sich“ wurde bei Anspruchs-Ende + 3 s abgeschnitten).
            letzte_aeusserung = max((float(it["end"]) for it in (tk.zeilen or [])
                                     if it.get("art") == "spiel" and float(it["end"]) <= s1), default=letzter_anspruch)
            out_s = max(in_s + 1.0, min(s1, max(letzter_anspruch, letzte_aeusserung) + 3.0))
        # Verantwortungs-Plage: ab dem ersten eigenen Beat, nur Beats, deren Span im Segment liegt —
        # so richtet `alternativen_fuer_plan` jeden Beat genau EINMAL und an sichtbarer Stelle aus.
        ab_beat = eigene[0] if idx > 0 else min(alle_beats)
        verantwortlich = [b for b in alle_beats
                          if b >= ab_beat and (b not in spans_alle or float(spans_alle[b]["start"]) < out_s - 0.5)]
        if not verantwortlich:
            verantwortlich = sorted(eigene)
        bereits |= plage
        art = "dialog" if any(b in dialog_beats for b in verantwortlich) else "stumm"
        zeilen = sorted({dialog_nr_je_beat[b] for b in verantwortlich if b in dialog_nr_je_beat})
        nr += 1
        e = Eintrag(nr, sz.nummer, str(tk.clip_id), c.dateiname, tk.einstellung, tk.slate_take,
                    round(in_s, 2), round(out_s, 2), zeilen, art,
                    f"Szenen-Master aus Einstellung {tk.einstellung} T{tk.slate_take} — läuft durch "
                    f"({out_s - in_s:.0f} s, deckt B{min(verantwortlich)}–B{max(verantwortlich)})"
                    + (f", Ketten-Glied {idx + 1}/{len(kette)}" if len(kette) > 1 else ""),
                    [])
        e.beleg = ["Take-Score: " + next(x[7] for x in kandidaten if x[1] is tk),
                   f"Spielfenster {s0:.1f}–{s1:.1f} s (Anspiel-Barriere bis Spielende)"]
        if leerbild_beleg:
            e.beleg.append(leerbild_beleg)
        e.beats = verantwortlich
        # Nachklang (stille Handlung nach der letzten harten Zeile) → gewichtete Szenen-Blende
        letzte_zeile_ende = max((float(it["end"]) for it in (tk.zeilen or [])
                                 if it.get("art") == "spiel" and it.get("skript_zeile_nr") is not None
                                 and float(it["end"]) <= out_s), default=None)
        if letzte_zeile_ende is not None and out_s - letzte_zeile_ende >= 4.0:
            e.beleg.append(f"Nachklang: {out_s - letzte_zeile_ende:.1f} s stille Handlung nach der letzten Zeile")
        segmente.append(((ab_beat,), e))
        protokoll_zeile = (f"Szenen-Master: {tk.einstellung} T{tk.slate_take} {in_s:.1f}–{out_s:.1f} s"
                           + (f" (Glied {idx + 1})" if len(kette) > 1 else ""))
        protokoll.append(protokoll_zeile)
    return segmente, nr, protokoll


def erzeuge_schnittplan(db, sk: Skript, name: str | None = None, parameter: dict | None = None) -> Schnittplan:
    parameter = {"modus": "rohschnitt", "coverage_wechsel": True, "stumm_max_s": STUMM_MAX, "insert_dauer_s": INSERT_DAUER,
                 "max_segment_s": MAX_SEGMENT_S, **(parameter or {})}
    master_modus = parameter.get("modus") == "master"
    fein = parameter.get("modus") == "feinschnitt" or master_modus
    if fein:
        # Master-Modus: keine Cutaway-Einschnitte in V1 — Coverage lebt komplett auf den Alternativ-Spuren
        parameter.setdefault("cutaways", not master_modus)
        parameter.setdefault("fade_s", 0.4)
        parameter["stumm_max_s"] = min(float(parameter.get("stumm_max_s", STUMM_MAX)), 40.0)
    clips = {c.id: c for c in db.query(Clip).all()}
    framing_je_clip: dict = {}
    personen_je_clip: dict = {}
    for s_ in db.query(Szene.clip_id, Szene.framing, Szene.analyse_visuelle).all():
        if s_[1] and s_[0] not in framing_je_clip:
            framing_je_clip[s_[0]] = s_[1]
        if isinstance(s_[2], dict) and isinstance(s_[2].get("personen"), int) and s_[0] not in personen_je_clip:
            personen_je_clip[s_[0]] = s_[2]["personen"]
    takes_je_szene = _takes_je_szene(db)
    max_segment = float(parameter.get("max_segment_s", MAX_SEGMENT_S))
    # Gesichter ↔ Figuren (für Reaktionsschnitte): cluster_id → Skript-Figur
    try:
        from backend.core.database import GesichtsCluster
        figur_je_cluster = {str(g.id): (g.name_skript or "").upper() for g in db.query(GesichtsCluster).filter(GesichtsCluster.skript_id == sk.id).all() if g.name_skript}
    except Exception:  # noqa: BLE001
        figur_je_cluster = {}
    ctxs = {c.skript_szene_id: c for c in db.query(SzenenKontext).all()}
    eintraege: list[Eintrag] = []
    luecken: list[dict] = []
    nr = 0

    for sz in sk.szenen:
        tks = takes_je_szene.get(sz.id, [])
        dialog = [z for z in sz.zeilen if z.art == "dialog"]
        aktionen = [z for z in sz.zeilen if z.art == "aktion"]
        if not tks:
            luecken.append({"szene": sz.nummer, "grund": "keine Takes im Material"})
            continue
        max_spiel = max(((t.spiel_ende_s or 0) - (t.spiel_start_s or 0)) for t in tks)
        je_einst: dict[str, list[TakeKontext]] = defaultdict(list)
        for t in tks:
            je_einst[t.einstellung or "?"].append(t)
        beste: dict[str, TakeKontext] = {e: _bester_take(l, max_spiel) for e, l in je_einst.items()}
        beste = {e: t for e, t in beste.items() if t is not None}

        # ── Inserts: Szene ohne Dialog, kurze Clips → Skript-Erwähnungen zuordnen ──
        if not dialog and all((clips[t.clip_id].dauer or 0) < 45 for t in tks if t.clip_id in clips):
            nr = _inserts(sz, tks, clips, aktionen, eintraege, nr, parameter, db=db)
            continue

        # ── Dialog-Einstellungen vs. stumme Einstellungen ──
        dialog_einst = {e for e, t in beste.items() if any(i.get("skript_zeile_id") for i in (t.zeilen or []))}
        stumm_einst = [e for e in beste if e not in dialog_einst]

        segmente_szene: list[tuple[tuple, Eintrag]] = []   # (sortkey, eintrag)
        beat_modus = bool(parameter.get("beats", True)) and any(t.takt for t in tks)
        beat_protokoll: list[str] = []
        gedeckte_aktionen_beats: set[int] = set()

        if beat_modus and master_modus:
            master = None
            segmente_szene, nr, beat_protokoll = _plane_szene_master(sz, tks, clips, framing_je_clip, parameter, nr)
            # Aktionen der gedeckten Beats gelten als im Bild erledigt (kein zusätzliches Stumm-Segment in V1)
            from backend.core.skript.beats import beats_fuer_szene as _bfs
            gedeckte_beat_nrs = {b for _, e_ in segmente_szene for b in e_.beats}
            gedeckte_aktionen_beats = {a for b_ in _bfs(sz) if b_.nr in gedeckte_beat_nrs for a in b_.aktionen}
            for h in beat_protokoll:
                luecken.append({"szene": sz.nummer, "grund": h})
        elif beat_modus:
            master = None
            segmente_szene, nr, gedeckte_aktionen_beats, beat_protokoll = _plane_szene_beats(
                sz, tks, clips, beste, je_einst, framing_je_clip, parameter, fein, nr, luecken, max_spiel)
            for h in beat_protokoll:
                luecken.append({"szene": sz.nummer, "grund": h})
        elif dialog and dialog_einst:
            master = max(dialog_einst, key=lambda e: ((beste[e].abdeckung or 0), (beste[e].spiel_ende_s or 0) - (beste[e].spiel_start_s or 0)))
            zeiten = {e: _zeilen_zeiten(beste[e]) for e in dialog_einst}
            aktuell = master
            offen: Eintrag | None = None
            letzte_figur = None
            # Wie viele Zeilen deckt jede Einstellung? (1–3 = „dediziertes“ Detail-/Gegenschuss-Stück für genau diese Zeilen)
            zeilen_je_einst = {e: len(zeiten[e]) for e in dialog_einst}
            # „Teil“ der Szene: nur bei dreistufiger Klappe (5.1.1 / 5.2.1) ist die zweite Zahl ein Szenen-TEIL (chronologisch);
            # bei zweistufiger (2.1 / 2.2) ist die zweite Zahl die Einstellung derselben Handlung (Coverage) → kein Teil.
            dreistufig = any(len(_einst_key(e)) >= 3 for e in dialog_einst)
            teil = lambda e: (_einst_key(e)[:2] if dreistufig else _einst_key(e)[:1])      # noqa: E731
            hoechster_teil: tuple = ()
            letzte_zeile_nr = None
            for z in dialog:
                zid = str(z.id)
                kandidaten = [e for e in dialog_einst if zid in zeiten[e]]
                # Schwache Einzeltreffer (Score < 0,62) einer Nicht-Master-Einstellung sind kein Grund, den Master zu
                # verlassen (z. B. „Wo bist du?“ ↔ „are you there?“ 0,55 in einem Take der Schluss-Szene).
                schwach = [e for e in kandidaten if not (e == aktuell or e == master or max(sc for _, _, _, sc in zeiten[e][zid]) >= WECHSEL_MIN_SCORE)]
                kandidaten = [e for e in kandidaten if e not in schwach]
                # Narrativ monoton: ist die Szene schon in einem späteren Teil (5.2.x), führt kein Weg zurück nach 5.1.x —
                # außer es gibt sonst nichts (dann bleibt es eine Lücke, kein Rücksprung).
                if hoechster_teil and kandidaten:
                    vorwaerts = [e for e in kandidaten if teil(e) >= hoechster_teil]
                    if vorwaerts:
                        kandidaten = vorwaerts
                    else:
                        luecken.append({"szene": sz.nummer, "zeile": z.nr, "figur": z.figur, "text": z.text,
                                        "grund": f"nur in früherem Szenenteil ({', '.join(kandidaten)}) gefunden — kein Rücksprung hinter {'.'.join(map(str, hoechster_teil))}"})
                        letzte_figur = z.figur
                        continue
                if not kandidaten:
                    luecken.append({"szene": sz.nummer, "zeile": z.nr, "figur": z.figur, "text": z.text,
                                    "grund": ("nur schwacher Treffer (< 0,62) in " + ", ".join(schwach) + " — nicht übernommen") if schwach else
                                             "in keinem Take gefunden (nicht gedreht, improvisiert oder Transkript-Treffer < Schwelle)"})
                    letzte_figur = z.figur
                    continue
                wechsel = False
                rang = lambda e: FRAMING_RANG.get(framing_je_clip.get(beste[e].clip_id), 0)   # noqa: E731
                wechsel_grund = ""
                if aktuell in kandidaten:
                    wahl = aktuell
                    andere = [e for e in kandidaten if e != aktuell]
                    dediziert = [e for e in andere if 1 <= zeilen_je_einst.get(e, 0) <= 3
                                 and max(sc for _, _, _, sc in zeiten[e][zid]) >= 0.68]
                    # Dediziertes Stück: eine Einstellung, die nur diese 1–3 Zeilen deckt, wurde FÜR diese Zeilen gedreht
                    # (Detail/Gegenschuss „Wir brauchen dich“ in 3.2) → dort hin, bevorzugt engere Kadrage
                    if dediziert and zeilen_je_einst.get(aktuell, 0) > 3:
                        wahl = max(dediziert, key=lambda e: (rang(e), max(sc for _, _, _, sc in zeiten[e][zid]))); wechsel = True
                        wechsel_grund = f"dedizierte Einstellung {wahl} für diese Zeile(n) ({zeilen_je_einst[wahl]} Zeilen gedeckt)"
                    # klassische Coverage: bei Sprecherwechsel auf eine andere deckende Einstellung gehen
                    elif parameter.get("coverage_wechsel") and letzte_figur is not None and z.figur != letzte_figur and andere:
                        wahl = max(andere, key=lambda e: (rang(e), beste[e].abdeckung or 0)); wechsel = True
                        wechsel_grund = f"Sprecherwechsel ({letzte_figur} → {z.figur}) → Gegeneinstellung {wahl}"
                    # Rhythmus: zu lange auf einer Einstellung → wechseln, wenn eine andere die Zeile sicher deckt
                    # (bevorzugt engere Kadrage: Nah vor Halbnah vor Totale)
                    elif offen is not None and andere and (zeiten[aktuell][zid][0][0] - offen.in_s) > max_segment:
                        wahl = max(andere, key=lambda e: (rang(e), beste[e].abdeckung or 0)); wechsel = True
                        wechsel_grund = f"Rhythmus: > {max_segment:.0f} s auf {aktuell} → Wechsel auf {wahl}"
                else:
                    wahl = master if master in kandidaten else max(kandidaten, key=lambda e: (rang(e), beste[e].abdeckung or 0))
                    wechsel = True
                    wechsel_grund = (f"zurück auf Master {wahl}" if wahl == master else
                                     f"Einstellung {wahl} deckt diese Zeile ({aktuell} nicht)")
                tk = beste[wahl]; clip = clips[tk.clip_id]
                treffer = zeiten[wahl][zid]
                hv, hn = (0.3, 0.5) if fein else (HANDLE_VOR, HANDLE_NACH)
                t_in = max(0.0, min(s for s, _, _, _ in treffer) - hv)
                t_out = min(float(clip.dauer or 1e9), max(e for _, e, _, _ in treffer) + hn)
                # Handlung zwischen zwei Zeilen: steht im Skript eine Aktion dazwischen („goes to the kitchen … comes back
                # with two cups“) und zeigt der Take in der Pause Bewegung, bleibt die Pause drin (bis AKTION_LUECKE_MAX);
                # ohne Skript-Aktion wird eng geschnitten (LUECKE_MAX).
                aktion_dazwischen = letzte_zeile_nr is not None and any(a.nr > letzte_zeile_nr and a.nr < z.nr for a in aktionen)
                erlaubt = LUECKE_MAX
                if offen is not None and aktion_dazwischen and offen.clip_id == str(tk.clip_id):
                    if _bewegung_im_fenster(clip, offen.out_s, t_in):
                        erlaubt = AKTION_LUECKE_MAX
                if offen is not None and not wechsel and offen.clip_id == str(tk.clip_id) and t_in - offen.out_s <= erlaubt:
                    if t_in - offen.out_s > LUECKE_MAX:
                        offen.beleg.append(f"Handlung laut Skript zwischen Z{letzte_zeile_nr} und Z{z.nr} behalten ({t_in - offen.out_s:.0f} s Bewegung)")
                    offen.out_s = max(offen.out_s, t_out); offen.zeilen.append(z.nr)
                    offen.beleg.append(f"Z{z.nr} {z.figur}: „{treffer[0][2][:60]}“ @{treffer[0][0]:.0f}s")
                else:
                    nr += 1
                    fr = framing_je_clip.get(tk.clip_id)
                    fr_txt = f" [{fr}]" if fr else ""
                    grund = (wechsel_grund if wechsel_grund else
                             ("Master-Einstellung (höchste Skript-Abdeckung)" if wahl == master else f"Einstellung {wahl} (deckt diese Zeile)")) + fr_txt
                    belege = [f"Z{z.nr} {z.figur}: „{treffer[0][2][:60]}“ @{treffer[0][0]:.0f}s"]
                    # Regel 2: vor der ERSTEN Skriptzeile der Szene improvisierte Spiel-Sätze desselben Takes gehören dazu
                    if not any(e_.art == "dialog" for _, e_ in segmente_szene):
                        imp_start, imp_texte = _improvisation_davor(tk, t_in)
                        if imp_start is not None and t_in - imp_start >= 1.0:
                            belege.append(f"Improvisiert vor Z{z.nr} eingeschlossen ({t_in - imp_start + hv:.0f} s): "
                                          + " · ".join(f"„{x[:40]}“" for x in imp_texte[:4]))
                            t_in = max(0.0, imp_start - hv)
                    offen = Eintrag(nr, sz.nummer, str(tk.clip_id), clip.dateiname, wahl, tk.slate_take, t_in, t_out, [z.nr],
                                    "dialog", grund, belege)
                    segmente_szene.append(((z.nr, 0, ()), offen))
                aktuell = wahl
                letzte_figur = z.figur
                letzte_zeile_nr = z.nr
                hoechster_teil = max(hoechster_teil, teil(wahl)) if hoechster_teil else teil(wahl)
            # (4) Mini-Schnipsel: unter 2,5 s auf 2,5 s verlängern (Take gibt es her), unter 1,5 s weglassen
            gefiltert = []
            for k_, e_ in segmente_szene:
                d_ = e_.out_s - e_.in_s
                if d_ < 2.5:
                    c_ = clips.get(uuid.UUID(e_.clip_id))
                    e_.out_s = min(float(c_.dauer or e_.out_s) if c_ else e_.out_s, e_.in_s + 2.5)
                    if e_.out_s - e_.in_s < 1.5:
                        continue
                gefiltert.append((k_, e_))
            segmente_szene = gefiltert
        else:
            master = None

        # ── stumme Einstellungen einsortieren ──
        dialog_nums = sorted(_einst_key(e) for e in dialog_einst)
        cutaway_quellen: list[tuple[TakeKontext, Clip, float, float]] = []   # (tk, clip, in, out) für Feinschnitt-Cutaways
        aktions_kandidaten: dict[int, list] = defaultdict(list)              # a_nr → [(ja, tk, clip, a, b, e, key, text)]
        for e in sorted(stumm_einst, key=_einst_key):
            # Stumme Einstellung: der Take mit den meisten im Bild bestätigten Skript-Aktions-Frames gewinnt
            # (Bildprüfung), sonst das allgemeine Ranking.
            def _solide_ja(t: TakeKontext) -> int:
                return sum(int(a.get("ja") or 0) for a in (t.aktionen or {}).values()
                           if a.get("spans") and any(sp[1] - sp[0] >= float(a.get("schritt") or 5.0) * 1.5 for sp in a["spans"]))
            kand = je_einst.get(e, [beste[e]])
            if any(_solide_ja(t) > 0 for t in kand):
                tk = max(kand, key=lambda t: (_solide_ja(t), take_score(t, max_spiel)[0]))
            else:
                tk = beste[e]
            clip = clips.get(tk.clip_id)
            if clip is None:
                continue
            t_in, t_out, fenster_grund = _spiel_fenster(tk, clip, float(parameter.get("stumm_max_s", STUMM_MAX)))
            if beat_modus:
                # Beat-Modus: stumme Takes sind über ihren Takt bereits Quellen-Kandidaten; hier nur Cutaway-Material sammeln
                if fein and (t_out - t_in) <= 14.0:
                    cutaway_quellen.append((tk, clip, t_in, t_out))
                continue
            # Skript-gesteuert (Bildprüfung): zeigt der Take eine Skript-Aktion, gilt deren Zeitfenster — in Skript-
            # Reihenfolge — statt des Bewegungsfensters. Gibt es mehrere Aktionen, werden sie als eigene Segmente gelegt.
            akt_fenster = _aktions_fenster(tk, sz, t_in, t_out, float(parameter.get("stumm_max_s", STUMM_MAX)), fein, clip)
            if akt_fenster:
                k = _einst_key(e)
                for fi, (a, b, a_nr, a_text) in enumerate(akt_fenster):
                    ja = int(((tk.aktionen or {}).get(str(a_nr)) or {}).get("ja") or 0)
                    # erst sammeln — dieselbe Skript-Aktion soll nur EINMAL gezeigt werden (bester Bild-Beleg gewinnt),
                    # sonst sehen wir „den Fall ins Leere“ dreimal aus drei Einstellungen
                    aktions_kandidaten[a_nr].append((ja, tk, clip, a, b, e, (a_nr, 1, k + (fi,)), a_text))
                if fein and (t_out - t_in) <= 14.0:
                    cutaway_quellen.append((tk, clip, t_in, t_out))
                continue
            if fein and segmente_szene and (t_out - t_in) <= 14.0:
                # Kurze stumme Einstellung (Detail: Finger, Gesicht) in einer Dialogszene = Cutaway-Material, kein eigener Block
                cutaway_quellen.append((tk, clip, t_in, t_out))
                continue
            if fein and (t_out - t_in) > 24.0:
                # Feinschnitt: lange Handlung ohne Dialog → Höhepunkte (bis 3 Fenster an Bewegungs-Maxima), nicht der Block
                proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
                if proxy.exists():
                    fenster = AK.hoehepunkte(AK.kurve(str(proxy)), t_in, t_out, max_gesamt=(40.0 if not segmente_szene else 24.0))
                    if len(fenster) >= 1:
                        k = _einst_key(e)
                        for fi, (a, b) in enumerate(fenster):
                            nr += 1
                            bild = next((bv.get("beschreibung") for bv in (tk.bildverlauf or []) if bv.get("t") is not None and a - 8 <= float(bv["t"]) <= b + 8), None)
                            key = _stumm_key(k + (fi,), k, dialog_nums, dialog_einst, beste)
                            segmente_szene.append((key, Eintrag(nr, sz.nummer, str(tk.clip_id), clip.dateiname, e, tk.slate_take, a, b, [],
                                                                "stumm", f"Höhepunkt {fi+1}/{len(fenster)} der Handlung ohne Dialog (Einstellung {e}, Bewegungsmaximum)",
                                                                [f"Bild: {bild[:80]}" if bild else "Bewegungsmaximum"])))
                        continue
            if t_out - t_in < 3.0:
                # Mini-Schnipsel bringen im Rohschnitt nichts — auf 3 s strecken (soweit der Take es hergibt) oder weglassen
                t_out = min(float(clip.dauer or t_out), t_in + 3.0)
                if t_out - t_in < 2.0:
                    continue
            # Position: vor der ersten Dialog-Einstellung → vor Zeile 0; nach der letzten → ans Ende; sonst dazwischen
            k = _einst_key(e)
            key = _stumm_key(k, k, dialog_nums, dialog_einst, beste)
            nr += 1
            bild = (tk.bildverlauf or [{}])[0].get("beschreibung") if tk.bildverlauf else None
            segmente_szene.append((key, Eintrag(nr, sz.nummer, str(tk.clip_id), clip.dateiname, e, tk.slate_take, t_in, t_out, [],
                                                "stumm", f"Handlung ohne Dialog (Einstellung {e}), bester Take · {fenster_grund}",
                                                [f"Bild @{t_in:.0f}s: {bild[:80]}" if bild else "keine Bildbeschreibung"])))
        # Regel 1 — Szenen-Eröffnung: die Aktion(en) VOR der ersten Dialogzeile kommen aus dem Take des ersten Dialog-
        # Segments, wenn der dort genug Spiel vor der ersten Zeile hat (Schlafen → Aufwachen → erste Worte in EINEM Bild).
        # Erst wenn der Master kein Pre-Dialog-Spiel hat, darf eine andere Einstellung die Eröffnung liefern.
        bestaetigte_aktionen: set[int] = set(gedeckte_aktionen_beats)
        if dialog and not beat_modus:
            erste_nr = min(z.nr for z in dialog)
            eroeffnung_aktionen = [a.nr for a in aktionen if a.nr < erste_nr]
            dialog_segs = [e_ for _, e_ in segmente_szene if e_.art == "dialog"]
            if eroeffnung_aktionen and dialog_segs:
                erstes = min(dialog_segs, key=lambda e_: (min(e_.zeilen) if e_.zeilen else 10**6, e_.in_s))
                tk_e = beste.get(erstes.einstellung)
                c_e = clips.get(uuid.UUID(erstes.clip_id)) if tk_e is not None else None
                if tk_e is not None and c_e is not None:
                    h = _szenen_eroeffnung(c_e, erstes, tk_e, float(parameter.get("stumm_max_s", STUMM_MAX)))
                    if h:
                        a_txt = ", ".join(f"A{a}" for a in eroeffnung_aktionen)
                        erstes.beleg.insert(0, f"{h} — deckt {a_txt}")
                        erstes.grund = f"Szenen-Eröffnung + {erstes.grund}"
                        for a_nr in eroeffnung_aktionen:
                            if a_nr in aktions_kandidaten:
                                aktions_kandidaten.pop(a_nr)
                            bestaetigte_aktionen.add(a_nr)
        # Je Skript-Aktion genau ein Segment (bester Bild-Beleg), höchstens so viele wie Aktionen
        for a_nr, kand in aktions_kandidaten.items():
            ja, tk, clip, a, b, e, key, a_text = max(kand, key=lambda x: (x[0], x[4] - x[3]))
            nr += 1
            bestaetigte_aktionen.add(a_nr)
            segmente_szene.append((key, Eintrag(nr, sz.nummer, str(tk.clip_id), clip.dateiname, e, tk.slate_take, a, b, [],
                                                "stumm", f"Skript-Aktion A{a_nr} im Bild bestätigt (VQA, {ja} Frames): „{a_text[:60]}“ — Einstellung {e}",
                                                [f"Bildprüfung: Aktion A{a_nr} {a:.0f}–{b:.0f}s" + (f" · {len(kand)} Einstellungen zeigen sie, beste gewählt" if len(kand) > 1 else "")])))
        segmente_szene.sort(key=lambda x: x[0])
        szenen_eintraege: list[Eintrag] = []
        for _, e in segmente_szene:
            if e.art == "dialog":
                c = clips.get(uuid.UUID(e.clip_id)) if not isinstance(e.clip_id, uuid.UUID) else clips.get(e.clip_id)
                if c is not None:
                    if not beat_modus:
                        _vor_nachlauf(c, e, beste.get(e.einstellung), sz, dialog, fein)
                        _dialog_segment_bereinigen(c, e)
                    else:
                        # Beat-Modus: nur die sichtbare Klappe am Anfang; das generische Ende-Bereinigen entfällt —
                        # es fraß den skriptgemäßen ABGANG (Fred durch die Tür) als vermeintlichen Ausstieg. Enden
                        # regeln Phasen-Schnitt, Anschluss-/Schluss-Auslauf samt Wächtern.
                        _dialog_segment_bereinigen(c, e, nur_anfang=True)
            elif beat_modus and e.art == "stumm":
                # Stumme Master-Segmente (Ketten-Glieder ohne Dialog-Beat) brauchen denselben
                # Klappen-Schutz am Anfang wie Dialog-Segmente.
                c = clips.get(uuid.UUID(e.clip_id)) if not isinstance(e.clip_id, uuid.UUID) else clips.get(e.clip_id)
                if c is not None:
                    _dialog_segment_bereinigen(c, e, nur_anfang=True)
            szenen_eintraege.append(e)
        if fein and parameter.get("cutaways") and cutaway_quellen:
            szenen_eintraege, nr = _cutaways_einfuegen(szenen_eintraege, cutaway_quellen, clips, beste, sz, nr, ausser=bestaetigte_aktionen)
        if fein and figur_je_cluster and dialog:
            szenen_eintraege, nr = _reaktionen_einfuegen(szenen_eintraege, tks, clips, beste, sz, nr, figur_je_cluster, personen_je_clip)
        if fein and szenen_eintraege:
            f = float(parameter.get("fade_s", 0.4))
            f_schwer = float(parameter.get("fade_schwer_s", 1.0))
            erste = next((e for e in szenen_eintraege if not e.audio_only and e.spur == 1), None)
            letzte = next((e for e in reversed(szenen_eintraege) if not e.audio_only and e.spur == 1), None)
            if erste: erste.fade_in = f
            if letzte:
                # Gewichtete Szenen-Blende (Nutzer 20.08.): endet die Szene mit einem Schluss-Auslauf
                # (= stille Handlung nach der letzten Zeile — Lamento, Abgang, reicher Nachklang),
                # trägt die Frontière dramatisches Gewicht → längere Ausblende (Dip Richtung Schwarz).
                # Deterministischer Proxy statt LLM-Urteil: der Auslauf-Beleg existiert nur, wenn der
                # Planer die Handlung nach der letzten Zeile tatsächlich verlängert hat.
                schwer = any(str(b).startswith(("Schluss-Auslauf:", "Nachklang:")) for b in (letzte.beleg or []))
                letzte.fade_out = f_schwer if schwer else f
        eintraege.extend(szenen_eintraege)

    # Nummern final durchzählen + absolute Timeline-Positionen: Hauptspur sequenziell; Ton-Brücken (audio_only) liegen
    # parallel zum Cutaway, den sie begleiten (gleiche tl_start wie der vorangehende Cutaway).
    cursor = 0.0
    letzte_cutaway_start = None
    letzter_master: Eintrag | None = None
    for i, e in enumerate(eintraege, 1):
        e.nr = i
        if e.spur >= 2 and e.overlay_offset is not None:
            # Schnitt-Overlay (Cutaway/Reaktion auf V2): parallel zum vorangehenden Master-Segment, kein Cursor-Vorschub
            basis = letzter_master.tl_start if letzter_master is not None and letzter_master.tl_start is not None else cursor
            e.tl_start = round(basis + e.overlay_offset, 3)
            continue
        if e.audio_only:
            e.tl_start = letzte_cutaway_start if letzte_cutaway_start is not None else cursor
            continue
        e.tl_start = cursor
        if e.art == "cutaway":
            letzte_cutaway_start = cursor
        if e.spur == 1:
            letzter_master = e
        cursor += e.dauer
    gesamt = round(cursor, 2)
    # ── Alternativen-Stapel (Feinschnitt, Nutzer-Logik 20.08.): je Beat des Masters die besten Passagen ANDERER
    # Takes auf V2/V3 legen — stumm (video_only), am Beat ausgerichtet. Der Cutter vergleicht per Spur-Ein/Ausblenden,
    # behält die beste, löscht den Rest. Kein Cursor-Vorschub: Alternativen liegen PARALLEL zum Master.
    if fein and parameter.get("alternativen", True):
        try:
            ausschluss: set = set()
            basis = 3
            if master_modus:
                # Geschwister zuerst (ganze Szene im anderen Winkel, V2/V3, anker-synchronisiert) —
                # ihre Einstellungen tauchen NICHT zusätzlich als Kurz-Extrakte auf; Extrakte ab V4.
                geschw = geschwister_fuer_plan(eintraege, takes_je_szene, clips, sk,
                                               max_pro_szene=int(parameter.get("geschwister_pro_szene", 2)))
                for e in geschw:
                    e.nr = len(eintraege) + 1
                    eintraege.append(e)
                    ausschluss.add((e.szene, e.einstellung or "?"))
                basis = 4
            alt = alternativen_fuer_plan(eintraege, takes_je_szene, clips, sk,
                                         max_pro_beat=int(parameter.get("alternativen_pro_beat", 2)),
                                         basis_spur=basis, ausschluss_einst=ausschluss)
            for e in alt:
                e.nr = len(eintraege) + 1
                eintraege.append(e)
            # Klappen-Schutz für Overlay-Spuren (Geschwister + Extrakte): beginnt ein Block in den
            # ersten 20 s seines Takes, sichtbare Klappe überspringen — der Sync-Moment wandert mit
            # (Kopf-Trim verschiebt tl_start um dasselbe Delta, der Rest bleibt synchron).
            behalten: list[Eintrag] = []
            for e in eintraege:
                if e.spur < 2 or e.art != "alternative" or e.in_s >= 20.0:
                    behalten.append(e)
                    continue
                c = clips.get(uuid.UUID(e.clip_id)) if not isinstance(e.clip_id, uuid.UUID) else clips.get(e.clip_id)
                if c is None:
                    behalten.append(e)
                    continue
                alt_in = e.in_s
                _dialog_segment_bereinigen(c, e, nur_anfang=True)
                delta = e.in_s - alt_in
                if delta > 0 and e.tl_start is not None:
                    e.tl_start = round(e.tl_start + delta, 3)
                if e.out_s - e.in_s >= 1.5:
                    behalten.append(e)
            eintraege[:] = behalten
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Alternativen-/Geschwister-Stapel fehlgeschlagen: {e}")
    plan = Schnittplan(id=uuid.uuid4(), skript_id=sk.id, name=name or f"Rohschnitt {sk.titel or sk.name}",
                       parameter=parameter, eintraege=[e.als_dict() for e in eintraege],
                       statistik={"eintraege": len(eintraege), "dauer_s": gesamt, "luecken": luecken,
                                  "szenen": len(sk.szenen), "szenen_mit_material": len({e.szene for e in eintraege})})
    db.add(plan)
    db.commit()
    return plan


def _cutaways_einfuegen(eintraege: list[Eintrag], quellen: list, clips: dict, beste: dict, sz: SkriptSzene, nr: int, ausser: set | None = None) -> tuple[list[Eintrag], int]:
    """Feinschnitt-Cutaways — NUR skript-motiviert: steht zwischen zwei Dialogzeilen eines Segments eine Skript-Aktion
    (A-Zeile), und zeigt ein Take DERSELBEN Szene diese Aktion im Bild (Bildprüfung) — bevorzugt ein kurzer Detail-Take,
    sonst ein anderer Take als der Master —, dann wird dort (an der Sprechpause nach der vorangehenden Zeile) ein
    Cutaway von 2,5–3,5 s gesetzt, der Master-Ton läuft als Brücke weiter. Ohne Bildbeleg kein Cutaway."""
    aktionen = [z for z in sz.zeilen if z.art == "aktion"]
    dialog = [z for z in sz.zeilen if z.art == "dialog"]
    # Aktions-Nachweise je Szene: aktion_nr → [(tk, clip, a, b, ist_detail)]
    nachweise: dict[int, list] = defaultdict(list)
    for q_tk, q_clip, q_in, q_out in quellen:
        for nr_s, info in (q_tk.aktionen or {}).items():
            for a, b in info.get("spans") or []:
                nachweise[int(nr_s)].append((q_tk, q_clip, float(a), float(b), True))
    for e_name, tk in beste.items():
        c = clips.get(tk.clip_id)
        if c is None:
            continue
        for nr_s, info in (tk.aktionen or {}).items():
            for a, b in info.get("spans") or []:
                nachweise[int(nr_s)].append((tk, c, float(a), float(b), False))
    out: list[Eintrag] = []
    for e in eintraege:
        if e.art != "dialog" or len(e.zeilen) < 2 or e.dauer < 10.0:
            out.append(e); continue
        tk = beste.get(e.einstellung)
        units = sorted((float(i["start"]), float(i["end"]), i.get("skript_zeile_nr")) for i in (tk.zeilen or []) if tk and i.get("art") == "spiel" and i.get("skript_zeile_id") and e.in_s <= float(i["start"]) <= e.out_s)
        gesetzt = False
        for z_prev, z_next in zip(e.zeilen, e.zeilen[1:]):
            dazwischen = [a for a in aktionen if z_prev < a.nr < z_next and a.nr not in (ausser or set())]
            if not dazwischen:
                continue
            kandidaten = []
            for a in dazwischen:
                for (q_tk, q_clip, qa, qb, detail) in nachweise.get(a.nr, []):
                    if str(q_clip.id) == e.clip_id:
                        continue        # nicht aus dem eigenen Master-Take schneiden
                    # Phasen-Kontinuität: hat der Quell-Take Zeilen, muss das Fenster zwischen z_prev und z_next dieses Takes liegen
                    q_units = [(float(i["start"]), float(i["end"]), i.get("skript_zeile_nr")) for i in (q_tk.zeilen or []) if i.get("art") == "spiel" and i.get("skript_zeile_id")]
                    if q_units:
                        prev_end = max((b_ for _, b_, n_ in q_units if n_ is not None and n_ <= z_prev), default=None)
                        next_start = min((a_ for a_, _, n_ in q_units if n_ is not None and n_ >= z_next), default=None)
                        if prev_end is None and next_start is None:
                            continue
                        lo = prev_end if prev_end is not None else 0.0
                        hi = next_start if next_start is not None else float(q_clip.dauer or qb)
                        qa, qb = max(qa, lo - 1.0), min(qb, hi + 1.0)
                        if qb - qa < 2.0:
                            continue
                    kandidaten.append((detail, qb - qa, a, q_tk, q_clip, qa, qb))
            if not kandidaten:
                continue
            kandidaten.sort(key=lambda x: (not x[0], -x[1]))
            detail, _, a, q_tk, q_clip, qa, qb = kandidaten[0]
            # Schnittpunkt: Ende des letzten Satzes von z_prev (+ kleine Pause)
            ende_prev = max((u[1] for u in units if u[2] == z_prev), default=None)
            start_next = min((u[0] for u in units if u[2] == z_next), default=None)
            if ende_prev is None or start_next is None:
                continue
            cut_at = ende_prev + 0.15
            cut_len = min(3.5, max(2.5, (start_next - ende_prev) - 0.2)) if start_next - ende_prev >= 1.0 else 2.5
            if cut_at - e.in_s < 2.0 or e.out_s - (cut_at + cut_len) < 2.0:
                continue
            q_len = min(cut_len, max(2.0, qb - qa))
            q_start = qa + max(0.0, (qb - qa - q_len) / 2)
            # V1 bleibt ROUGH MASTER (Nutzer-Regel 20.08.): der Master wird NICHT zerschnitten — der Cutaway liegt
            # als stummer Overlay auf Spur 2 ÜBER dem laufenden Master (oberste Spur gewinnt das Bild, der Ton läuft
            # per Audio-Fallthrough weiter). Ton-Brücken entfallen damit komplett.
            cut = Eintrag(0, e.szene, str(q_clip.id), q_clip.dateiname, q_tk.einstellung, q_tk.slate_take, q_start, q_start + q_len, [], "cutaway",
                          f"Cutaway (Overlay V2) für Skript-Aktion A{a.nr} „{a.text[:50]}“ — im Bild bestätigt in Einstellung {q_tk.einstellung}; Ton läuft vom Master weiter",
                          [f"Bildprüfung: A{a.nr} {qa:.0f}–{qb:.0f}s in {q_clip.dateiname[7:21]}"], video_only=True)
            cut.spur = 2
            cut.overlay_offset = round(cut_at - e.in_s, 3)
            out.append(e)
            out.append(cut)
            gesetzt = True
            break
        if not gesetzt:
            out.append(e)
    return out, nr


def _wer_im_bild(tk: TakeKontext, a: float, b: float, figur_je_cluster: dict[str, str]) -> dict[str, float]:
    """Figur → Anteil des Fensters [a,b], in dem ihr Gesicht erkannt ist (aus TakeKontext.gesichter-Spans)."""
    out: dict[str, float] = defaultdict(float)
    if b <= a:
        return {}
    for gid, info in (tk.gesichter or {}).items():
        fig = figur_je_cluster.get(gid)
        if not fig:
            continue
        ueberl = 0.0
        for s0, s1 in info.get("spans") or []:
            ueberl += max(0.0, min(b, float(s1)) - max(a, float(s0)))
        out[fig] = max(out[fig], ueberl / (b - a))
    return dict(out)


def _phase_fenster(tk: TakeKontext, znr: int, frac: float, naechste_nr: int | None) -> tuple[float, float] | None:
    """Zeitfenster im Take `tk`, das derselben Skript-Phase entspricht: innerhalb der Zeile `znr` (bei Anteil `frac`) bzw.
    zwischen `znr` und der nächsten Zeile. Liefert None, wenn der Take die Zeile nicht enthält (keine Phasenaussage möglich)."""
    units = [(float(i["start"]), float(i["end"]), i.get("skript_zeile_nr")) for i in (tk.zeilen or []) if i.get("art") == "spiel" and i.get("skript_zeile_id")]
    if not units:
        return None
    z_units = [(a, b) for a, b, n in units if n == znr]
    if not z_units:
        return None
    za, zb = min(a for a, _ in z_units), max(b for _, b in z_units)
    n_units = [a for a, b, n in units if naechste_nr is not None and n == naechste_nr and a > zb]
    ende = min(n_units) if n_units else zb + 8.0
    mitte = za + frac * (zb - za)
    return (max(za, mitte - 6.0), max(mitte + 6.0, min(ende, mitte + 12.0)))


def _reaktionen_einfuegen(eintraege: list[Eintrag], tks: list[TakeKontext], clips: dict, beste: dict, sz: SkriptSzene, nr: int,
                          figur_je_cluster: dict[str, str], personen_je_clip: dict | None = None) -> tuple[list[Eintrag], int]:
    """Echte Reaktionsschnitte (Feinschnitt): spricht Figur A eine lange Zeile (≥ 7 s) und die aktuelle Einstellung zeigt den
    Zuhörer B NICHT (oder kaum), während eine andere Einstellung DERSELBEN Szene B im Bild hat (Gesichtserkennung) — dann
    2,5 s Reaktion auf B (Bild ohne Ton, Master-Ton läuft) bei ~60 % der Zeile. B muss laut Skript in der Szene sein
    (Sprecher-Cue oder Regie-Nennung). Höchstens eine Reaktion je Segment, nie aus dem eigenen Take."""
    zeilen = {z.nr: z for z in sz.zeilen if z.art == "dialog"}
    figuren_szene = {(z.figur or "").upper() for z in sz.zeilen if z.art == "dialog" and z.figur}
    for z in sz.zeilen:
        if z.regie:
            for w in re.findall(r"[A-Z][a-z]{3,}", z.regie):
                figuren_szene.add(w.upper())
    out: list[Eintrag] = []
    benutzt: set[tuple[str, int]] = set()          # (clip_id, gerundete Quellzeit) — dieselbe Reaktion nie zweimal
    dreistufig = any(len(_einst_key(t.einstellung)) >= 3 for t in tks)
    teil_von = lambda e_: (_einst_key(e_)[:2] if dreistufig else _einst_key(e_)[:1])   # noqa: E731
    for e in eintraege:
        if e.art != "dialog" or e.dauer < 7.0 or not e.zeilen:
            out.append(e); continue
        tk = beste.get(e.einstellung)
        if tk is None:
            out.append(e); continue
        # die längste Zeile im Segment (Sprecher A) mit ihren Zeiten
        units = [(float(i["start"]), float(i["end"]), i.get("skript_zeile_nr")) for i in (tk.zeilen or []) if i.get("art") == "spiel" and i.get("skript_zeile_id") and e.in_s <= float(i["start"]) <= e.out_s]
        if not units:
            out.append(e); continue
        je_zeile: dict[int, tuple[float, float]] = {}
        for a, b, znr in units:
            if znr is None:
                continue
            lo, hi = je_zeile.get(znr, (a, b))
            je_zeile[znr] = (min(lo, a), max(hi, b))
        znr, (za, zb) = max(je_zeile.items(), key=lambda kv: kv[1][1] - kv[1][0])
        if zb - za < 7.0:
            out.append(e); continue
        sprecher = (zeilen[znr].figur or "").upper() if znr in zeilen else ""
        zuhoerer = [f for f in figuren_szene if f and f != sprecher]
        if not zuhoerer:
            out.append(e); continue
        cut_at = za + 0.6 * (zb - za)
        folgende = sorted(n for n in zeilen if n > znr)
        naechste_nr = folgende[0] if folgende else None
        # Aktuelle Einstellung: zeigt sie einen Zuhörer schon? (dann ist keine Reaktion nötig)
        jetzt = _wer_im_bild(tk, cut_at - 1.5, cut_at + 1.5, figur_je_cluster)
        if any(jetzt.get(b_, 0.0) >= 0.5 for b_ in zuhoerer):
            out.append(e); continue
        # Kandidaten: andere Takes der Szene, deren Gesichts-Spans einen Zuhörer zeigen und den Sprecher NICHT
        best = None
        for t2 in tks:
            # nie aus dem eigenen Take, nie aus derselben Einstellung (Jump-Cut), nie aus einem anderen Szenen-TEIL
            # (Tür-Dialog 5.1.x bekommt keine Reaktion aus der Geist-Szene 5.2.x)
            if str(t2.clip_id) == e.clip_id or not t2.gesichter or (t2.einstellung or "") == (e.einstellung or ""):
                continue
            if teil_von(t2.einstellung) != teil_von(e.einstellung):
                continue
            c2 = clips.get(t2.clip_id)
            if c2 is None:
                continue
            # Phasen-Kontinuität: zeigt der Quell-Take dieselbe Zeile, darf die Reaktion nur aus deren Zeitfenster kommen
            # (sonst sehen wir die Zuhörerin bereits im Zustand einer späteren Zeile). Ohne Zeilen im Quell-Take (reiner
            # Zuhörer-Take, z. B. Geist) ist die Phase frei.
            phase = _phase_fenster(t2, znr, 0.6, naechste_nr)
            if phase is None and any(i.get("skript_zeile_id") for i in (t2.zeilen or [])):
                continue
            for gid, info in t2.gesichter.items():
                fig = figur_je_cluster.get(gid)
                if fig not in zuhoerer:
                    continue
                for s0, s1 in info.get("spans") or []:
                    if phase is not None:
                        s0, s1 = max(s0, phase[0]), min(s1, phase[1])
                    if s1 - s0 < 2.5:
                        continue
                    # innerhalb des Spans mehrere Kandidaten-Zeitpunkte (alle 2,5 s), unbenutzte bevorzugen
                    t_k = s0 + 1.25
                    while t_k + 1.25 <= s1:
                        key = (str(c2.id), int(t_k // 2.5))
                        if key not in benutzt:
                            anteil_sprecher = _wer_im_bild(t2, t_k - 1.25, t_k + 1.25, figur_je_cluster).get(sprecher, 0.0)
                            if anteil_sprecher <= 0.3:
                                score = (s1 - s0)
                                if best is None or score > best[0]:
                                    best = (score, t2, c2, fig, t_k)
                                break
                        t_k += 2.5
        if best is None:
            out.append(e); continue
        _, t2, c2, fig, mitte = best
        q_len = 2.5
        q_start = max(0.0, mitte - q_len / 2)
        benutzt.add((str(c2.id), int(mitte // 2.5)))
        # NUR Einzel-Quellen (1 Person = echte Reaktion). Ein ZWEITER Zweier derselben Paarung wäre ein Achsen-Sprung
        # ohne Mehrwert (links/rechts kippt — Nutzer-Befund 20.08.) → kein Overlay.
        einzel = (personen_je_clip or {}).get(c2.id, None) == 1
        art_txt = "Reaktion"
        if not einzel:
            out.append(e); continue
        if cut_at - e.in_s < 2.0 or e.out_s - (cut_at + q_len) < 2.0:
            out.append(e); continue
        # V1 bleibt ROUGH MASTER: Reaktion als stummer Overlay auf Spur 2 (Bild oben gewinnt, Ton fällt zum Master durch)
        cut = Eintrag(0, e.szene, str(c2.id), c2.dateiname, t2.einstellung, t2.slate_take, q_start, q_start + q_len, [], "cutaway",
                      f"{art_txt} (Overlay V2): {fig.capitalize()} hört {sprecher.capitalize()} zu — Gesicht in Einstellung {t2.einstellung} erkannt, gleiche Szenen-Phase (Z{znr}); Ton läuft vom Master weiter",
                      [f"Gesichter: {fig} in {c2.dateiname[7:21]} {q_start:.0f}–{q_start + q_len:.0f}s"], video_only=True)
        cut.spur = 2
        cut.overlay_offset = round(cut_at - e.in_s, 3)
        out.append(e)
        out.append(cut)
    return out, nr


def _insert_cluster(tks: list[TakeKontext], clips: dict, db, schwelle: float = 0.90) -> list[list[TakeKontext]]:
    """Takes desselben Inserts (gleiches Motiv, wiederholt gedreht) über das CLIP-Embedding der Szene zusammenfassen
    (Kosinus ≥ 0,90 — gemessen: Wiederholungen 0,97, verschiedene Motive ≤ 0,85)."""
    import math
    emb: dict = {}
    for s_ in db.query(Szene).filter(Szene.clip_id.in_([t.clip_id for t in tks])).order_by(Szene.szenen_nr).all():
        if s_.clip_id not in emb and s_.clip_embedding:
            emb[s_.clip_id] = s_.clip_embedding
    def cos(a, b):
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
    cluster: list[list[TakeKontext]] = []
    for t in sorted(tks, key=lambda x: (x.slate_take or 0)):
        e = emb.get(t.clip_id)
        ziel = None
        if e is not None:
            for cl in cluster:
                e2 = emb.get(cl[0].clip_id)
                if e2 is not None and cos(e, e2) >= schwelle:
                    ziel = cl; break
        (ziel.append(t) if ziel is not None else cluster.append([t]))
    return cluster


def _inserts(sz: SkriptSzene, tks: list[TakeKontext], clips: dict, aktionen, eintraege: list[Eintrag], nr: int, parameter: dict, db=None) -> int:
    """Szene ohne Dialog aus kurzen Clips (Inserts): Takes desselben Motivs werden zu EINEM Kandidaten (Doppel-Takes
    raus), der Skript-Aktionstext in Erwähnungen zerlegt, je Erwähnung das ähnlichste Motiv (Bildbeschreibung, bge-m3)
    gewählt — jedes Motiv höchstens einmal —, in Skript-Reihenfolge geschnitten; Anfang nach sichtbarer Klappe."""
    text = " ".join(z.text for z in aktionen)
    erwaehnungen = [t.strip(" .") for t in re.split(r",|\band\b|\bund\b|;|\(|\)", text) if len(t.strip()) > 6][:10]
    cluster = _insert_cluster([t for t in tks if t.clip_id in clips], clips, db) if db is not None else [[t] for t in tks if t.clip_id in clips]
    # Vertreter je Cluster: manuell „circled“ > späterer Take (Drehkonvention: spätere Takes sind die besseren) > länger
    vertreter: list[tuple[TakeKontext, Clip, int]] = []
    for cl in cluster:
        best = max(cl, key=lambda t: ((t.bewertung == "circled"), t.bewertung != "ng", (t.slate_take or 0), float(clips[t.clip_id].dauer or 0)))
        vertreter.append((best, clips[best.clip_id], len(cl)))
    beschr = [" ".join(b.get("beschreibung", "") for b in (t.bildverlauf or [])[:3]) or c.dateiname for t, c, _ in vertreter]
    e_b = A.embed(beschr)
    e_m = A.embed(erwaehnungen)
    benutzt: set = set()
    dauer_insert = float(parameter.get("insert_dauer_s", INSERT_DAUER))
    for mi, erw in enumerate(erwaehnungen):
        best, best_s, best_i = None, 0.0, -1
        for ci, (t, c, n) in enumerate(vertreter):
            if ci in benutzt:
                continue
            s_ = A._cos(e_m[mi], e_b[ci])
            if s_ > best_s:
                best, best_s, best_i = (t, c, n), s_, ci
        if best is None or best_s < 0.45:
            continue
        t, c, n = best
        benutzt.add(best_i)
        d = float(c.dauer or 0)
        t_in = 0.5 if d > dauer_insert + 1 else 0.0
        hinweise: list[str] = []
        proxy = PROXY_DIR / f"{clip_stem(c)}_proxy.mp4"
        if proxy.exists() and proxy.stat().st_size > 0:
            k = AK.kurve(str(proxy))
            s2, h = AK.anfang_nach_klappe(k, t_in)
            if h and s2 < d - 1.5:
                t_in = s2; hinweise.append(h)
        kv = _klappe_sichtbar_bis(c)
        if kv is not None and t_in < kv < d - 1.5:
            t_in = kv; hinweise.append(f"sichtbare Klappe (CLIP-Bildcheck) bis {kv:.1f} s übersprungen")
        t_out = min(d, t_in + dauer_insert) if d - t_in > dauer_insert + 1 else d
        nr += 1
        eintraege.append(Eintrag(nr, sz.nummer, str(c.id), c.dateiname, t.einstellung, t.slate_take, t_in, t_out, [], "insert",
                                 f"Insert für Skript-Erwähnung „{erw[:40]}“ (Ähnlichkeit {best_s:.2f})" + (f" · {n} Takes desselben Motivs → Take {t.slate_take}" if n > 1 else ""),
                                 [f"Bild: {beschr[best_i][:90]}"] + hinweise))
    return nr
