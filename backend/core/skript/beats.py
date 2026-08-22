"""Beats (Szenen-Takt) — die dramaturgische Einheit zwischen Skriptzeile und Szene.

Warum: Zwei Takes derselben Szene sind zwei vollständige Aufführungen mit anderen Worten. Wer nach *Skriptzeilen* schneidet,
legt „Z4 aus Take A“ neben „Z6 aus Take B“ — und zeigt denselben Moment zweimal (Wiederholung), weil die Schauspielerin die
Zeile in Take A viermal improvisiert hat und in Take B anders. Der Plan muss daher pro **Beat** denken: ein Beat = ein Moment
der Handlung, wird genau einmal gezeigt, aus genau einer Quelle, und Take-Wechsel passieren an Beat-Grenzen (Phasen-Kontinuität).

1. `beats_fuer_szene(sz)` — deterministisch aus dem Skript: Aktionen VOR der ersten Dialogzeile = Eröffnungs-Beat; jede
   Dialogzeile + die Aktionen seit der vorigen Dialogzeile = ein Beat; Aktionen NACH der letzten Dialogzeile = Schluss-Beat;
   Szene ohne Dialog = ein Beat je Aktion.
2. `takt_fuer_take(tk, clip, beats)` — monotone Segmentierung des Takes in Beats (Viterbi): Ereignisse = Spiel-Sätze (aligniert
   → Anker des Beats; improvisiert → semantische Nähe bge-m3 zu den Beat-Texten; kurze Rufe „Hey“/„Yuri?“ = Fortsetzung) +
   bestätigte Skript-Aktionen aus der Bildprüfung (VQA-Fenster → Beat der Aktion). Rückwärts im Skript geht es nie, Beats
   dürfen fehlen (nicht gedreht / Einstellung deckt nur einen Teil). Ergebnis je Take: [{beat, start, end, kern, anker, …}].
3. `berechne_takt(db, sk)` — persistiert Beats in `szenen_kontext.takt` und Take-Segmentierungen in `take_kontext.takt`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict

from backend.core.config import PROXY_DIR
from backend.core.database import Clip, Skript, SkriptSzene, TakeKontext, SzenenKontext
from backend.core.skript import alignment as A
from backend.core.skript import aktivitaet as AK
from backend.core.medien import clip_stem

logger = logging.getLogger("cinassist.skript.beats")

SEM_MIN = 0.48          # darunter ist ein improvisierter Satz kein Hinweis auf einen bestimmten Beat (Fortsetzung)
SEM_MARGE = 0.04        # … und der beste Beat muss sich vom zweitbesten abheben
SEM_BODEN = 0.35        # Ähnlichkeit minus Boden = Gewicht
ANKER_GEWICHT = 1.5     # alignierte Skriptzeile = stärkste Evidenz (+ Alignment-Score)
VQA_GEWICHT = 0.2       # je bestätigtem Bild-Frame (10-s-Schritt) — ein 40-s-Fenster (4 Frames) wiegt weniger als ein Anker
ANKER_WEICH_MARGE = 0.06  # Anker gilt als mehrdeutig (weich), wenn ein anderer Beat dem GESAGTEN Text fast so ähnlich ist
POS_GEWICHT = 0.9       # Pass-2-Prior: erwartete Position eines Beats im Take (aus den Skript-Textanteilen)
SKIP_STRAFE = 0.25      # Viterbi: je übersprungenem Beat
START_STRAFE = 0.05     # Viterbi: je Beat, der vor dem ersten Ereignis übersprungen wird (Takes dürfen mitten in der Szene beginnen: 3.2, 5.2.x)
VOR_STRAFE = 0.05       # Viterbi: ein Beat weiter kostet ein wenig — ohne Evidenz bleibt man im Beat (Rufe = Fortsetzung)
FLACH = 0.0
EROEFFNUNG_RUHE_S = 6.0
EROEFFNUNG_MIN_S = 6.0
NACHLAUF_MAX_S = 18.0


@dataclass
class Beat:
    nr: int
    art: str                       # eroeffnung | dialog | schluss | aktion
    zeilen: list[int] = field(default_factory=list)      # alle Skript-Zeilennummern des Beats
    aktionen: list[int] = field(default_factory=list)
    dialog_nr: int | None = None
    figur: str | None = None
    text: str = ""                 # EN (Skript) — Dialog + Aktionen
    text_de: str = ""              # DE-Übersetzung der Dialogzeile

    def als_dict(self) -> dict:
        return asdict(self)

    @property
    def titel(self) -> str:
        if self.dialog_nr is not None:
            t = (self.text_de or "").strip() or self.text.strip()
            return f"B{self.nr} Z{self.dialog_nr} {self.figur or ''}: „{t[:60]}“"
        return f"B{self.nr} {self.art}: {self.text[:70]}"


def beats_fuer_szene(sz: SkriptSzene) -> list[Beat]:
    zeilen = sorted((z for z in sz.zeilen if z.art in ("dialog", "aktion")), key=lambda z: z.nr)
    dialoge = [z for z in zeilen if z.art == "dialog"]
    beats: list[Beat] = []
    if not dialoge:
        for z in zeilen:
            beats.append(Beat(len(beats), "aktion", [z.nr], [z.nr], None, None, z.text.strip()))
        return beats
    puffer: list = []
    erster_dialog = dialoge[0].nr
    for z in zeilen:
        if z.art == "aktion":
            puffer.append(z)
            continue
        if z.nr == erster_dialog and puffer:
            beats.append(Beat(len(beats), "eroeffnung", [a.nr for a in puffer], [a.nr for a in puffer], None, None,
                              " ".join(a.text.strip() for a in puffer)))
            puffer = []
        beats.append(Beat(len(beats), "dialog", [a.nr for a in puffer] + [z.nr], [a.nr for a in puffer], z.nr, z.figur,
                          (z.text.strip() + " " + " ".join(a.text.strip() for a in puffer)).strip(), (z.text_ziel or "").strip()))
        puffer = []
    if puffer:
        if len(dialoge) <= 1 and len(puffer) > 1:
            # Quasi-stumme Szene (≤ 1 Dialogzeile): die Schluss-Aktionen NICHT in einen
            # Sammel-Beat pressen — jede Skript-Aktion wird ihr eigener Beat, nur die
            # letzte bleibt „schluss“ (daran hängt der Schluss-Auslauf im Planer).
            # Befund Szene 4: Mülleimer/Drogen, TV-Flackern und Geist teilten sich EINEN
            # Beat → eine Quelle für drei Momente, zwei fehlten im Master.
            for a in puffer[:-1]:
                beats.append(Beat(len(beats), "aktion", [a.nr], [a.nr], None, None, a.text.strip()))
            a = puffer[-1]
            beats.append(Beat(len(beats), "schluss", [a.nr], [a.nr], None, None, a.text.strip()))
        else:
            beats.append(Beat(len(beats), "schluss", [a.nr for a in puffer], [a.nr for a in puffer], None, None,
                              " ".join(a.text.strip() for a in puffer)))
    return beats


def beats_aus_dicts(d: list[dict] | None) -> list[Beat]:
    out = []
    for x in d or []:
        out.append(Beat(int(x["nr"]), x.get("art", "dialog"), list(x.get("zeilen") or []), list(x.get("aktionen") or []),
                        x.get("dialog_nr"), x.get("figur"), x.get("text") or "", x.get("text_de") or ""))
    return out


# ───────────────────────── Ereignisse + Viterbi ─────────────────────────

_KURZ_RE = re.compile(r"^[\s\W]*([\w'’-]+[\s\W]*){1,2}$", re.UNICODE)


def _ist_kurzruf(text: str) -> bool:
    """„Hey.“ · „Yuri?“ · „Babe, hallo.“ · „Hallo?“ — Rufe/Vokative ohne Inhalt = Fortsetzung des aktuellen Beats."""
    t = (text or "").strip()
    return bool(_KURZ_RE.match(t)) or len(t) <= 4


def _ist_klappe(text: str) -> bool:
    """Sprech-Klappe im Transkript: „2 .1, Date 2.“ · „In 5 .1 .1.“ · „Scene 3 .2, Take 4.“ · „Take 3.“"""
    t = (text or "").strip()
    if not t or re.match(r"^[\s\d.,:/-]+$", t) or re.match(r"^\s*\d+\s*[.,]\s*\d*", t):
        return True
    tl = t.lower()
    return bool(re.search(r"\b(take|scene|szene|slate|klappe|date)\b", tl)) or bool(re.search(r"\b\d+\s*\.\s*\d+", tl))


def _ereignisse(tk: TakeKontext, beats: list[Beat]) -> list[dict]:
    """[{t0, t1, vektor[len(beats)], art, text, beleg}] chronologisch. vektor = Evidenz je Beat (≥ 0)."""
    n = len(beats)
    beat_von_zeile: dict[int, int] = {}
    beat_von_aktion: dict[int, int] = {}
    for b in beats:
        for z in b.zeilen:
            beat_von_zeile[z] = b.nr
        for a in b.aktionen:
            beat_von_aktion[a] = b.nr
    ev: list[dict] = []
    improv: list[tuple[int, str]] = []
    for it in tk.zeilen or []:
        if it.get("art") != "spiel" or _ist_klappe(it.get("text") or ""):
            continue
        t0, t1 = float(it["start"]), float(it["end"])
        text = str(it.get("text") or "").strip()
        znr = it.get("skript_zeile_nr")
        if it.get("skript_zeile_id") and znr is not None and int(znr) in beat_von_zeile:
            v = [FLACH] * n
            v[beat_von_zeile[int(znr)]] = ANKER_GEWICHT + float(it.get("score") or 0.0)
            ev.append({"t0": t0, "t1": t1, "vektor": v, "art": "anker", "text": text, "zeile": int(znr),
                       "beat": beat_von_zeile[int(znr)], "score": float(it.get("score") or 0.0),
                       "beleg": f"Z{znr} @{t0:.0f}s ({float(it.get('score') or 0):.2f}) „{text[:40]}“"})
        elif _ist_kurzruf(text):
            ev.append({"t0": t0, "t1": t1, "vektor": [FLACH] * n, "art": "ruf", "text": text, "beleg": ""})
        else:
            ev.append({"t0": t0, "t1": t1, "vektor": None, "art": "improv", "text": text, "beleg": ""})
            improv.append((len(ev) - 1, text))
    # Improvisation + mehrdeutige Anker: semantische Nähe zu den Beat-Texten (cross-lingual bge-m3)
    anker_idx = [i for i, e in enumerate(ev) if e["art"] == "anker"]
    if improv or anker_idx:
        beat_texte = [(b.text + (" " + b.text_de if b.text_de else "")).strip() or "-" for b in beats]
        embs = A.embed([t for _, t in improv] + [ev[i]["text"] for i in anker_idx] + beat_texte)
        e_improv, e_anker, e_beats = embs[:len(improv)], embs[len(improv):len(improv) + len(anker_idx)], embs[len(improv) + len(anker_idx):]
        # Weiche Anker: „Wach doch auf!“ passt lexikalisch auf Z4 („time to wake up“) UND Z6 („This isn't funny, wake up!“) —
        # der Aligner musste sich für eine Zeile entscheiden. Ist ein anderer Beat dem gesagten Text fast so ähnlich, wird die
        # Evidenz VERTEILT statt erzwungen; Monotonie + Positions-Prior + eindeutige Anker entscheiden dann die Grenze.
        for i, e_emb in zip(anker_idx, e_anker):
            kb = ev[i]["beat"]
            if e_emb is None:
                continue
            sims = [A._cos(e_emb, eb) for eb in e_beats]
            andere = [b for b in range(n) if b != kb and sims[b] >= max(0.45, sims[kb] - ANKER_WEICH_MARGE)]
            if not andere:
                continue
            score = ev[i]["vektor"][kb] - ANKER_GEWICHT
            v = [FLACH] * n
            v[kb] = ANKER_GEWICHT * 0.5 + 0.5 * score
            for b in andere:
                v[b] = ANKER_GEWICHT * 0.35
            ev[i]["vektor"] = v
            ev[i]["weich"] = True
            ev[i]["beleg"] += " · weich (auch ≈ B" + ",B".join(str(b) for b in andere[:3]) + ")"
        for (i, text), e in zip(improv, e_improv):
            sims = [A._cos(e, eb) for eb in e_beats]
            srt = sorted(sims, reverse=True)
            if not e or not sims or srt[0] < SEM_MIN or (len(srt) > 1 and srt[0] - srt[1] < SEM_MARGE):
                ev[i]["vektor"] = [FLACH] * n
                ev[i]["art"] = "ruf"
                continue
            v = [max(FLACH, s - SEM_BODEN) for s in sims]
            k = max(range(n), key=lambda j: sims[j])
            ev[i]["vektor"] = v
            ev[i]["beleg"] = f"improv ≈ B{k} ({sims[k]:.2f}) „{text[:40]}“ @{ev[i]['t0']:.0f}s"
            ev[i]["sem"] = (k, sims[k])
    # Bildprüfung: bestätigte Skript-Aktionen (mind. 2 Ja-Frames)
    for a_s, info in (tk.aktionen or {}).items():
        try:
            a_nr = int(a_s)
        except ValueError:
            continue
        if a_nr not in beat_von_aktion:
            continue
        schritt = float(info.get("schritt") or 5.0)
        for sp in info.get("spans") or []:
            if sp[1] - sp[0] < schritt * 1.5:
                continue
            # je Frame ein Ereignis, damit lange Fenster nicht über Beat-Grenzen hinweg „ziehen“
            t = float(sp[0]); erstes = True
            while t < float(sp[1]) - 1e-6:
                v = [FLACH] * n
                v[beat_von_aktion[a_nr]] = VQA_GEWICHT
                ev.append({"t0": t, "t1": min(float(sp[1]), t + schritt), "vektor": v, "art": "vqa", "text": "", "aktion": a_nr,
                           "beleg": f"VQA A{a_nr} {sp[0]:.0f}–{sp[1]:.0f}s" if erstes else ""})
                erstes = False
                t += schritt
    ev.sort(key=lambda x: (x["t0"], x["t1"]))
    return ev


def _viterbi(ev: list[dict], n: int) -> list[int]:
    if not ev or n == 0:
        return []
    NEG = -1e9
    score = [[NEG] * n for _ in ev]
    back = [[-1] * n for _ in ev]
    for k in range(n):
        score[0][k] = -START_STRAFE * k + ev[0]["vektor"][k]
    for t in range(1, len(ev)):
        for k in range(n):
            best, bj = NEG, -1
            for j in range(0, k + 1):
                s = score[t - 1][j] - (SKIP_STRAFE * (k - j - 1) if k > j + 1 else 0.0) - (VOR_STRAFE if k > j else 0.0)
                if s > best:
                    best, bj = s, j
            score[t][k] = best + ev[t]["vektor"][k]
            back[t][k] = bj
    k = max(range(n), key=lambda j: score[-1][j])
    pfad = [k]
    for t in range(len(ev) - 1, 0, -1):
        k = back[t][k]
        pfad.append(k)
    pfad.reverse()
    return pfad


def _spiel_grenzen(tk: TakeKontext, clip: Clip) -> tuple[float, float]:
    dauer = float(clip.dauer or 0.0)
    # Spielbeginn = nach der LETZTEN Klappe / dem letzten Produktions-Sprech VOR der ersten Spiel-Äußerung — egal wo im
    # Take. Ein „Bitte."/„Set." MITTEN im Take markiert die Grenze zwischen ANSPIELEN (vorgespielter Szenenanfang zum
    # Warmwerden — gehört NIE in den Schnitt) und dem gewollten Teil (Nutzer-Befund 20.08., 3.2/T3: Wieder-Reinkommen
    # vor „Bitte." @43,5 s landete im Master). Ohne Spiel-Äußerung (stumme Takes) gilt die Erste-Hälfte-Regel.
    erste_sprache = min((float(it["start"]) for it in (tk.zeilen or []) if it.get("art") == "spiel" and not _ist_klappe(it.get("text") or "")), default=None)
    start = 0.0
    for it in tk.zeilen or []:
        if not (it.get("art") in ("slate", "produktion") or _ist_klappe(it.get("text") or "")):
            continue
        ende_it = float(it["end"])
        if (erste_sprache is not None and ende_it <= erste_sprache) or (erste_sprache is None and ende_it < dauer * 0.5):
            start = max(start, ende_it + 0.3)
    if start == 0.0 and tk.spiel_start_s is not None and (erste_sprache is None or tk.spiel_start_s < erste_sprache - 1.0):
        start = float(tk.spiel_start_s)
    # Produktions-Sprech MITTEN im Take („Hallo?“ Richtung Crew, vor der Hälfte) darf den Einstieg nicht HINTER das
    # Spiel schieben (beobachtet: 4.3 T1 → Fenster 38–39 s statt 20–39 s). Einstieg nie hinter der ersten Spiel-Äußerung.
    if erste_sprache is not None and start > erste_sprache - 0.1:
        start = max(0.0, erste_sprache - 0.5)
    # Spielende = Clip-Ende, begrenzt durch Produktions-Sprech NACH der letzten Spiel-Äußerung — NICHT das letzte
    # transkribierte Wort: nach der letzten erkannten Replik kommt oft noch Spiel (leise gesprochenes Kleinzeug, das
    # der ASR entgeht, und die Abgangs-Handlung — Nutzer-Befund 20.08., 3.2/T3: Freds „blabla" + Abgang @54–58 s
    # waren unerreichbar). Was WIRKLICH ins Segment kommt, entscheidet der bewegungsbasierte Nachlauf.
    letzte_sprache = max((float(it["end"]) for it in (tk.zeilen or [])
                          if it.get("art") == "spiel" and not _ist_klappe(it.get("text") or "")), default=None)
    ende = max(0.0, dauer - 0.5)
    for it in tk.zeilen or []:
        if it.get("art") not in ("slate", "produktion"):
            continue
        anfang_it = float(it["start"])
        if (letzte_sprache is not None and anfang_it > letzte_sprache) or (letzte_sprache is None and anfang_it > dauer * 0.7):
            ende = min(ende, anfang_it - 0.3)
    return max(0.0, start), max(start + 1.0, ende)


VORLAUF_MAX_S = 10.0
VORGRIFF_MAX_S = 30.0   # Aktions-Vorgriff: so weit darf ein Beat mit Vor-Zeilen-Aktion in unbeanspruchtes Spiel zurückgreifen


def _bewegungs_schwelle(kurve) -> float:
    alle = sorted(v for _, v in kurve)
    med = alle[len(alle) // 2] if alle else 0.0
    return max(0.8, med * 1.3)


def _nachlauf(kurve, von: float, bis_max: float, sprache: list[tuple[float, float]], max_s: float = NACHLAUF_MAX_S) -> float:
    """Ende nach dem letzten Wort: solange Bewegung (oder Sprache) da ist, bis 2 s Ruhe, höchstens max_s / bis_max."""
    if not kurve:
        return min(bis_max, von + 0.8)
    schwelle = _bewegungs_schwelle(kurve)
    t = von + 0.8; ruhig = 0
    while t + 0.5 <= min(bis_max, von + 0.8 + max_s):
        v = [vv for tt, vv in kurve if t <= tt < t + 0.5]
        spricht = any(a < t + 0.5 and b > t for a, b in sprache)
        if (v and max(v) >= schwelle) or spricht:
            ruhig = 0
        else:
            ruhig += 1
            if ruhig >= 4:
                break
        t += 0.5
    neu = max(von + 0.8, t - ruhig * 0.5 + 0.6)
    return min(bis_max, neu)


def _vorlauf(kurve, bis: float, von_min: float, sprache: list[tuple[float, float]], max_s: float = VORLAUF_MAX_S) -> float:
    """Anfang vor dem ersten Wort: rückwärts, solange Bewegung/Sprache da ist, bis 3 s Ruhe, höchstens max_s / von_min."""
    if not kurve:
        return max(von_min, bis - 0.5)
    schwelle = _bewegungs_schwelle(kurve)
    t = bis - 0.3; ruhig = 0
    while t - 0.5 >= max(von_min, bis - max_s):
        v = [vv for tt, vv in kurve if t - 0.5 <= tt < t]
        spricht = any(a < t and b > t - 0.5 for a, b in sprache)
        if (v and max(v) >= schwelle) or spricht:
            ruhig = 0
        else:
            ruhig += 1
            if ruhig >= 6:
                break
        t -= 0.5
    neu = min(bis - 0.3, t + ruhig * 0.5 - 0.6)
    return max(von_min, neu)


def takt_fuer_take(tk: TakeKontext, clip: Clip, beats: list[Beat]) -> list[dict]:
    """Monotone Beat-Spans eines Takes. Je Span: {beat, start, end, kern:[a,b], grenze:[vor,nach], anker, sem, vqa, evidenz,
    staerke, ereignisse, belege}. `grenze` = lückenlose Aufteilung des Spiels (Grenze = Kern-Ende des vorigen / Kern-Anfang des
    nächsten Beats); `start`/`end` = dramaturgisch getrimmt (Vorlauf/Nachlauf nach Bewegung; Eröffnung = erste Bewegung − 6 s
    Ruhe). `evidenz` = der Beat ist im Take wirklich belegt (Anker / eindeutige Improvisation / eigene Bild-Aktion) — Beats ohne
    Evidenz sind Durchgangs-Beats (Handlung ohne Worte) und taugen nur als Fortsetzung, nicht als Quelle."""
    if not beats:
        return []
    spiel_start, spiel_ende = _spiel_grenzen(tk, clip)
    ev = _ereignisse(tk, beats)
    if not any(e["art"] != "vqa" for e in ev):
        # stummer Take: Spielende aus der Sprache nicht bestimmbar → bis kurz vor Clip-Ende
        spiel_ende = max(spiel_ende, float(clip.dauer or 0.0) - 1.0)
    # Bild-Frames außerhalb des Spiels (Einrichten vor der Klappe, Ausstieg am Ende) sind keine Evidenz
    ev = [e for e in ev if e["art"] != "vqa" or (e["t1"] > spiel_start - 0.5 and e["t0"] < spiel_ende + 0.5)]
    if not ev:
        return []
    pfad = _viterbi(ev, len(beats))
    # Pass 2 — Dauer-/Positions-Prior (semi-Markov-Näherung): jeder Beat hat eine erwartete Position im Take, proportional
    # zu seinem Skript-Textanteil (Replik + Aktionen), skaliert auf den vom Take abgedeckten Beat-Bereich (Takes dürfen
    # mitten in der Szene beginnen). Ereignisse werden zu der Position gezogen, an der ihr Beat laut Drehbuch liegt —
    # so wandert die Grenze B2→B3 dorthin, wo das Spiel kippt, statt dass ein Beat alle mehrdeutigen Rufe „aufsaugt“.
    sig = [k for e, k in zip(ev, pfad) if max(e["vektor"]) >= 0.3]
    if sig and max(sig) > min(sig):
        kmin, kmax = min(sig), max(sig)
        lo = min(e["t0"] for e in ev); hi = max(e["t1"] for e in ev)
        if hi - lo > 20.0:
            gew = [max(3, len((beats[k].text or "").split())) for k in range(len(beats))]
            tot = float(sum(gew[kmin:kmax + 1]))
            center: dict[int, float] = {}
            cum = 0.0
            for k in range(kmin, kmax + 1):
                center[k] = (cum + gew[k] / 2.0) / tot
                cum += gew[k]
            ev2 = []
            for e in ev:
                pos = (e["t0"] - lo) / max(1e-6, hi - lo)
                v2 = list(e["vektor"])
                for k in range(kmin, kmax + 1):
                    v2[k] -= POS_GEWICHT * abs(pos - center[k])
                ev2.append({**e, "vektor": v2})
            pfad = _viterbi(ev2, len(beats))
    je_beat: dict[int, list[dict]] = {}
    for e, k in zip(ev, pfad):
        je_beat.setdefault(k, []).append(e)
    ks = sorted(je_beat)
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    kurve = AK.kurve(str(proxy)) if proxy.exists() and proxy.stat().st_size > 0 else ()
    sprache_alle = [(e["t0"], e["t1"]) for e in ev if e["art"] != "vqa"]
    # Klappe/Einrichten am Anfang überspringen (wie die Eröffnungs-Regel): Spiel beginnt nach dem Klappen-Spike
    if kurve:
        s2, h = AK.anfang_nach_klappe(kurve, spiel_start)
        erster_kern = min((e["t0"] for e in ev if e["art"] != "vqa"), default=spiel_ende)
        if h and s2 < erster_kern - 2.0:
            spiel_start = max(spiel_start, s2)
    info: list[dict] = []
    for k in ks:
        evs = je_beat[k]
        basis = [e for e in evs if e["art"] != "vqa"] or evs
        info.append({"k": k, "evs": evs, "kern_a": min(e["t0"] for e in basis), "kern_b": max(e["t1"] for e in basis),
                     "sprache": any(e["art"] != "vqa" for e in evs)})
    out: list[dict] = []
    for i, it in enumerate(info):
        k, evs = it["k"], it["evs"]
        kern_a, kern_b = it["kern_a"], it["kern_b"]
        anker = any(e["art"] == "anker" and e.get("beat") == k for e in evs)
        weich = any(e["art"] == "anker" and e.get("weich") and e.get("beat") != k and e["vektor"][k] > 0 for e in evs)
        sem = max((e.get("sem", (None, 0.0))[1] for e in evs if e["art"] == "improv" and e.get("sem", (None,))[0] == k), default=0.0)
        vqa_eigen = any(e["art"] == "vqa" and e["vektor"][k] > 0 for e in evs)
        g_vor = spiel_start if i == 0 else max(spiel_start, info[i - 1]["kern_b"] + 0.3)
        g_nach = spiel_ende if i + 1 == len(info) else max(g_vor + 1.0, info[i + 1]["kern_a"] - 0.3)
        # Start
        if i == 0 and (beats[k].art == "eroeffnung" or (k == 0 and beats[k].art != "dialog") or (k == 0 and kern_a - spiel_start >= EROEFFNUNG_MIN_S)):
            region = [(t, v) for t, v in kurve if spiel_start <= t <= kern_a]
            af = AK.aktives_fenster(region, mindest_s=1.5) if region else None
            start = max(spiel_start, (af[0] - EROEFFNUNG_RUHE_S) if af is not None else kern_a - 12.0)
            eroeffnung = True
        else:
            start = _vorlauf(kurve, kern_a, g_vor, sprache_alle)
            eroeffnung = False
        start = min(start, kern_a)
        # Ende
        end = _nachlauf(kurve, kern_b, g_nach, sprache_alle)
        if end - start < 1.0:
            end = min(g_nach, start + 1.0) if g_nach - start >= 1.0 else start + 1.0
        # Eröffnungs-Beat ohne Bild-Beleg: belegt nur, wenn der Take wirklich Spiel vor der ersten Zeile hat UND danach mit
        # einer alignierten Zeile weitergeht (eine Aufführung von Anfang an — nicht irgendein Take des Szenen-Endes)
        folgt_anker = i + 1 < len(info) and any(e["art"] == "anker" and e["vektor"][info[i + 1]["k"]] - ANKER_GEWICHT >= 0.62 for e in info[i + 1]["evs"])
        evidenz = bool(anker or weich or sem > 0 or vqa_eigen or (eroeffnung and beats[k].art == "eroeffnung" and kern_a - spiel_start >= EROEFFNUNG_MIN_S and folgt_anker))
        staerke = sum(e["vektor"][k] for e in evs) + 0.1 * sum(1 for e in evs if e["art"] == "ruf")
        anker_ev = [e for e in evs if e["art"] == "anker" and e.get("beat") == k]
        anker_score = max((float(e.get("score") or 0.0) for e in anker_ev), default=0.0)
        eigene = [e for e in evs if e.get("beleg") and (e["vektor"][k] > 0)]
        out.append({"beat": k, "start": round(start, 2), "end": round(end, 2), "kern": [round(kern_a, 2), round(kern_b, 2)],
                    "grenze": [round(g_vor, 2), round(g_nach, 2)], "anker": anker, "sem": round(sem, 2), "vqa": vqa_eigen,
                    "anker_span": ([round(min(e["t0"] for e in anker_ev), 2), round(max(e["t1"] for e in anker_ev), 2)] if anker_ev else None),
                    "anker_score": round(anker_score, 2), "weich": weich,
                    "evidenz": evidenz, "eroeffnung": eroeffnung, "sprache": it["sprache"], "staerke": round(staerke, 2),
                    "ereignisse": len(evs), "belege": [e["beleg"] for e in eigene][:8]})
    # ── Aktions-Vorgriff: trägt ein Beat Skript-AKTIONEN vor seiner Replik (B2: „kommt mit zwei Tassen zurück“ + Z4),
    # beginnt seine Fenster aber erst an der Anker-Zeile, weil das Spiel davor nur aus Kurzrufen („Hier.“ „Bitte schön.“)
    # und Bewegung besteht (= null Beat-Evidenz), dann greift der Beat in diese UNBEANSPRUCHTE Zone zurück — begrenzt
    # durch das letzte Fenster MIT Evidenz, den Spielbeginn und VORGRIFF_MAX_S. Verschluckte evidenzlose Durchgangs-
    # Fenster werden gekürzt/entfernt. (Nutzer-Befund 20.08.: Tee-Servieren fehlte, Segment begann „schon sitzend“.)
    for i, sp in enumerate(out):
        b = beats[sp["beat"]]
        if not sp.get("evidenz") or b.art != "dialog" or not b.aktionen or sp.get("eroeffnung"):
            continue
        barriere = spiel_start
        for prev in out[:i]:
            if prev.get("evidenz"):
                barriere = max(barriere, float(prev["kern"][1]) + 0.3)
        alt_start = float(sp["start"])
        if alt_start - barriere < 1.5:
            continue
        region_rufe = [e for e in ev if e["art"] != "vqa" and barriere - 0.2 <= e["t0"] < alt_start - 0.2
                       and max(e["vektor"]) <= FLACH + 1e-9]
        region_kurve = [(t, v) for t, v in kurve if barriere <= t <= alt_start]
        af = AK.aktives_fenster(region_kurve, mindest_s=1.5) if region_kurve else None
        kandidaten_start = []
        if region_rufe:
            kandidaten_start.append(min(e["t0"] for e in region_rufe) - 0.5)
        if af is not None:
            kandidaten_start.append(af[0] - 1.0)
        if not kandidaten_start:
            continue
        neu = max(barriere, min(kandidaten_start), alt_start - VORGRIFF_MAX_S)
        if alt_start - neu < 1.5:
            continue
        sp["start"] = round(neu, 2)
        sp["vorgriff"] = round(alt_start - neu, 1)
        sp["belege"] = ([f"Aktions-Vorgriff {sp['vorgriff']:.0f}s: A{','.join(map(str, b.aktionen))} vor Z{b.dialog_nr}, "
                         f"Spiel ab {neu:.0f}s (Rufe/Bewegung, von keinem Anker beansprucht)"] + list(sp.get("belege") or []))[:8]
        # Durchgangs-Fenster ohne Evidenz, die jetzt überdeckt sind, kürzen bzw. entfernen
        for prev in out[:i]:
            if prev.get("evidenz"):
                continue
            if float(prev["start"]) >= neu - 0.05:
                prev["_entfernen"] = True
            elif float(prev["end"]) > neu:
                prev["end"] = round(neu, 2)
    out = [sp for sp in out if not sp.get("_entfernen")]
    # Synthetischer Eröffnungs-Beat: der Take beginnt direkt vor dem ersten Dialog-Beat, hat aber ≥ 8 s Spiel davor (Schlafen →
    # Aufwachen, Fred kommt herein) ohne Worte/Bild-Belege → das IST die Eröffnung (Regel 1), als Span ohne Ereignisse
    if out and beats and beats[0].art == "eroeffnung" and out[0]["beat"] == 1 and out[0]["anker"] and out[0].get("anker_score", 0) >= 0.62 \
            and out[0]["kern"][0] - spiel_start >= EROEFFNUNG_MIN_S:
        kern_a = out[0]["kern"][0]
        region = [(t, v) for t, v in kurve if spiel_start <= t <= kern_a]
        af = AK.aktives_fenster(region, mindest_s=1.5) if region else None
        start = max(spiel_start, (af[0] - EROEFFNUNG_RUHE_S) if af is not None else kern_a - 12.0)
        ende = max(start + 1.0, kern_a - 0.3)
        b0 = {"beat": 0, "start": round(start, 2), "end": round(ende, 2), "kern": [round(start, 2), round(ende, 2)],
              "grenze": [round(spiel_start, 2), round(ende, 2)], "anker": False, "sem": 0.0, "vqa": False, "anker_span": None, "anker_score": 0.0, "weich": False,
              "evidenz": True, "eroeffnung": True, "sprache": False, "staerke": 0.3, "ereignisse": 0,
              "belege": [f"Eröffnung ohne Worte: Spiel ab {spiel_start:.0f} s, erste Bewegung bei {(af[0] if af else kern_a - 6):.0f} s → {start:.0f}–{ende:.0f} s"]}
        out[0]["start"] = max(out[0]["start"], round(ende, 2)) if out[0]["start"] < ende else out[0]["start"]
        out[0]["eroeffnung"] = False
        out.insert(0, b0)
    return out


def berechne_takt(db, sk: Skript, nur_fehlende: bool = False, fortschritt=None) -> dict:
    """Beats je Szene (szenen_kontext.takt) + Beat-Spans je Take (take_kontext.takt). Gibt {szenen, takes} zurück."""
    clips = {c.id: c for c in db.query(Clip).all()}
    n_sz = n_tk = 0
    szenen = list(sk.szenen)
    for si, sz in enumerate(szenen):
        beats = beats_fuer_szene(sz)
        ctx = db.query(SzenenKontext).filter(SzenenKontext.skript_szene_id == sz.id).first()
        if ctx is None:
            ctx = SzenenKontext(skript_szene_id=sz.id)
            db.add(ctx)
        ctx.takt = [b.als_dict() for b in beats]
        n_sz += 1
        tks = db.query(TakeKontext).filter(TakeKontext.skript_szene_id == sz.id).all()
        for tk in tks:
            if nur_fehlende and tk.takt:
                continue
            c = clips.get(tk.clip_id)
            if c is None:
                continue
            try:
                tk.takt = takt_fuer_take(tk, c, beats)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Takt für {c.dateiname} fehlgeschlagen: {e}")
                tk.takt = []
            n_tk += 1
        db.commit()
        if fortschritt:
            fortschritt((si + 1) / max(1, len(szenen)), f"Beats Szene {sz.nummer}: {len(beats)} Beats, {len(tks)} Takes")
    return {"szenen": n_sz, "takes": n_tk}
