"""Matching-Kaskade (Stufe 1–4) — reine Logik auf Dataclasses, ohne DB, ohne ffmpeg.

Stufe 1  Timecode        — Intervall-Überlappung [tc_start, tc_start + dauer]      → sicher / plausibel
Stufe 2  Wellenform      — Kreuzkorrelation (nur mit Kamera-Scratch)              → bestätigt / plausibel
Stufe 3  Klappe          — Transienten in WAV + Video-Scratch                      → plausibel (≤ 0,6)
Stufe 4  Dateiname       — NUR Gruppierung + Warnungen; Zuordnung nur auf Wunsch   → unklar (≤ 0,3)

Jede Zuordnung trägt Methode, Konfidenz und einen lesbaren Grund. Mehrdeutigkeit
(ein Audio ↔ zwei Videos oder umgekehrt) wird NICHT aufgelöst, sondern als `unklar`
mit Kandidatenliste ausgegeben. Deterministisch: gleiche Eingabe → gleiche Ausgabe.

Was die Stufen NICHT garantieren, steht in backend/core/sync/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Callable, Optional

from .namen import NamensTeile

# ─── Schwellen ────────────────────────────────────────────────────────────
UEBERLAPPUNG_SICHER = 0.80        # Anteil an der kürzeren Dauer → sicher
KANDIDAT_MIN_RATIO = 0.20         # darunter zählt eine Überlappung nicht als Kandidat …
KANDIDAT_MIN_SEKUNDEN = 5.0       # … außer sie ist absolut lang genug
WELLENFORM_TOLERANZ_S = 0.10      # Wellenform bestätigt TC, wenn |Δ| ≤ 100 ms
KONFIDENZ_KLAPPE_MAX = 0.6
KONFIDENZ_DATEINAME_MAX = 0.3
MULTICAM_MIN_RATIO = 0.5          # Videos überlappen sich ≥ 50 % der kürzeren Dauer → parallel laufende Kameras

STATUS_SICHER = "sicher"
STATUS_PLAUSIBEL = "plausibel"
STATUS_UNKLAR = "unklar"
STATUS_VERWAIST = "verwaist"

METHODE_TIMECODE = "timecode"
METHODE_WAVEFORM = "waveform"
METHODE_KLAPPE = "klappe"
METHODE_DATEINAME = "dateiname"
METHODE_MANUELL = "manuell"


# ─── Eingabe ──────────────────────────────────────────────────────────────

@dataclass
class AssetInfo:
    id: str
    typ: str                              # "video" | "audio"
    dateiname: str
    dauer_s: float
    tc_start_s: Optional[float]           # Sekunden seit Mitternacht (None = kein verwertbarer TC)
    tc_rate: Optional[Fraction]
    tc_quelle: str                        # bwf | ixml | ltc | container | keine
    datum: Optional[date]                 # Drehtag (Gruppierung); None = unbekannt
    namen: NamensTeile
    scratch_kanal: Optional[int] = None   # Video: erster nicht-stiller Nicht-LTC-Kanal
    record_kanal: int = 0                 # Audio: Kanal für Transkription
    warnungen: list[str] = field(default_factory=list)

    @property
    def tc_ende_s(self) -> Optional[float]:
        return None if self.tc_start_s is None else self.tc_start_s + self.dauer_s


# ─── Ausgabe ──────────────────────────────────────────────────────────────

@dataclass
class LinkVorschlag:
    audio_id: str
    offset_s: float                       # audio_start − video_start (signiert, ms-genau)
    methode: str
    konfidenz: float
    begruendung: str
    kanal_fuer_transkription: int = 0
    warnungen: list[str] = field(default_factory=list)


@dataclass
class Kandidat:
    audio_id: str
    video_id: str
    offset_s: Optional[float]
    ueberlappung_s: float
    ueberlappung_ratio: float
    begruendung: str


@dataclass
class TakeVorschlag:
    video_id: Optional[str]
    status: str
    szene: Optional[int]
    plan: Optional[int]
    prise: Optional[int]
    links: list[LinkVorschlag] = field(default_factory=list)
    kandidaten: list[Kandidat] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)
    audio_ids_verwaist: list[str] = field(default_factory=list)   # nur bei video_id=None
    multicam_gruppe: Optional[str] = None      # gesetzt, wenn dieses Video mit anderen parallel lief (gleicher Ton)


@dataclass
class MatchErgebnis:
    takes: list[TakeVorschlag]
    statistik: dict
    warnungen: list[str] = field(default_factory=list)


# Injizierbare Stufen (Dateizugriff bleibt außerhalb der reinen Logik).
WaveformFn = Callable[[AssetInfo, AssetInfo], Optional["object"]]   # → waveform.KorrelationsErgebnis | None
KlappeFn = Callable[[AssetInfo, AssetInfo], Optional[tuple[float, str]]]  # → (offset_s, begruendung) | None


# ─── Hilfen ───────────────────────────────────────────────────────────────

def _fmt_tc(sek: Optional[float]) -> str:
    if sek is None:
        return "—"
    hh = int(sek // 3600) % 24
    mm = int(sek // 60) % 60
    ss = sek % 60
    return f"{hh:02d}:{mm:02d}:{ss:05.2f}".replace(".", ",")


def _rate_kompatibel(a: AssetInfo, v: AssetInfo) -> bool:
    if a.tc_rate is None or v.tc_rate is None:
        return True
    return abs(float(a.tc_rate) - float(v.tc_rate)) < 0.01


def _runde_ms(x: float) -> float:
    return round(x, 3)


def namens_warnungen(a: AssetInfo, v: Optional[AssetInfo]) -> list[str]:
    """Stufe 4 als Warnquelle: Nummern-Vergleich + unbekannte Markierung."""
    w: list[str] = []
    if a.namen.unbekannte_markierung:
        w.append(
            f"unbekannte_markierung: Audio-Name trägt „{a.namen.unbekannte_markierung}“ "
            f"(iXML CIRCLED=FALSE) — Bedeutung unbekannt, nicht interpretiert"
        )
    if v is None:
        return w
    an, vn = a.namen, v.namen
    if an.prise is not None and vn.prise is not None and an.prise != vn.prise:
        w.append(
            f"Take-Nummern verschoben: Audio {an.prise:03d} ↔ Video T{vn.prise:03d} "
            f"(Differenz {an.prise - vn.prise:+d}) — Zuordnung stützt sich auf Timecode, nicht auf den Namen"
        )
    if an.szene is not None and vn.szene is not None and an.szene != vn.szene:
        w.append(f"Szenen-Nummern widersprechen sich: Audio Szene {an.szene} ↔ Video Szene {vn.szene}")
    if an.plan is not None and vn.plan is not None and an.plan != vn.plan:
        w.append(f"Einstellungs-Nummern widersprechen sich: Audio Einstellung {an.plan} ↔ Video Einstellung {vn.plan}")
    return w


# ─── Stufe 1: Timecode ────────────────────────────────────────────────────

def drehtag_rang(videos: list[AssetInfo], audios: list[AssetInfo]):
    """Drehtag-Paarung über SZENEN-AFFINITÄT statt Datums-Gleichheit: Kamera- und Rekorder-Uhren
    können unterschiedliche ABSOLUTE Daten tragen (Befund Pinky Promise: Kamera „2024-09-27/28/29“,
    Rekorder „2023-11-17/18“; an Tag 2 wurde der Rekorder zudem auf 00:00 resettet → Tag-2-Videos
    kollidierten per Uhrzeit-TC mit Tag-1-WAVs, falsche Links mit 0,99 Konfidenz). Wahrheit = welche
    SZENEN an einem Tag gedreht/getont wurden (Namens-Parsing beider Seiten): Tage werden greedy nach
    gemeinsamen Szenen-Zählern gepaart; ungepaarte Tage (z. B. Nachdreh ohne Tonmensch) matchen NIE
    per TC. Fallback ohne Szenen-Signal: Rang-Paarung bei gleicher Tages-Anzahl, sonst None
    (→ alter Gleichheits-Guard)."""
    v_tage = sorted({v.datum for v in videos if v.datum})
    a_tage = sorted({a.datum for a in audios if a.datum})
    if not v_tage or not a_tage:
        return None, None
    from collections import Counter
    cv = {d: Counter(v.namen.szene for v in videos if v.datum == d and v.namen and v.namen.szene) for d in v_tage}
    ca = {d: Counter(a.namen.szene for a in audios if a.datum == d and a.namen and a.namen.szene) for d in a_tage}
    paare = sorted(((sum(min(cv[vd][s], ca[ad][s]) for s in set(cv[vd]) | set(ca[ad])), str(vd), str(ad), vd, ad)
                    for vd in v_tage for ad in a_tage), reverse=True)
    zug_v: dict = {}
    zug_a: dict = {}
    rang = 0
    for aff, _, _, vd, ad in paare:
        if aff <= 0 or vd in zug_v or ad in zug_a:
            continue
        zug_v[vd] = rang
        zug_a[ad] = rang
        rang += 1
    if zug_v:
        return zug_v, zug_a
    if len(v_tage) == len(a_tage):
        return {d: i for i, d in enumerate(v_tage)}, {d: i for i, d in enumerate(a_tage)}
    return None, None


def _tc_kandidaten(videos: list[AssetInfo], audios: list[AssetInfo]) -> tuple[list[Kandidat], list[str]]:
    kand: list[Kandidat] = []
    warn: list[str] = []
    rang_v, rang_a = drehtag_rang(videos, audios)
    for v in videos:
        if v.tc_start_s is None:
            continue
        for a in audios:
            if a.tc_start_s is None:
                continue
            if a.datum and v.datum:
                if rang_v is not None:
                    rv, ra = rang_v.get(v.datum), rang_a.get(a.datum)
                    if rv is None or ra is None or rv != ra:
                        continue
                elif a.datum != v.datum:
                    continue
            if not _rate_kompatibel(a, v):
                warn.append(f"TC-Rate-Konflikt: {a.dateiname} ({a.tc_rate}) ↔ {v.dateiname} ({v.tc_rate}) — Paar übersprungen")
                continue
            start = max(a.tc_start_s, v.tc_start_s)
            ende = min(a.tc_ende_s, v.tc_ende_s)
            ueb = ende - start
            if ueb <= 0:
                continue
            kuerzer = min(a.dauer_s, v.dauer_s) or 1.0
            ratio = ueb / kuerzer
            if ratio < KANDIDAT_MIN_RATIO and ueb < KANDIDAT_MIN_SEKUNDEN:
                continue
            offset = _runde_ms(a.tc_start_s - v.tc_start_s)
            kand.append(Kandidat(
                a.id, v.id, offset, ueb, ratio,
                f"Timecode: Audio {_fmt_tc(a.tc_start_s)} ({a.tc_quelle}) / Video {_fmt_tc(v.tc_start_s)} ({v.tc_quelle}), "
                f"Überlappung {ratio:.0%}, Offset {offset:+.2f} s".replace(".", ","),
            ))
    return kand, warn


# ─── Kaskade ──────────────────────────────────────────────────────────────

def matche(videos: list[AssetInfo], audios: list[AssetInfo], *,
           waveform_fn: Optional[WaveformFn] = None,
           klappe_fn: Optional[KlappeFn] = None) -> MatchErgebnis:
    videos = sorted(videos, key=lambda x: (x.dateiname, x.id))
    audios = sorted(audios, key=lambda x: (x.dateiname, x.id))
    a_by_id = {a.id: a for a in audios}
    v_by_id = {v.id: v for v in videos}
    global_warn: list[str] = []
    takes: list[TakeVorschlag] = []
    verknuepft_audio: set[str] = set()
    verknuepft_video: set[str] = set()
    stat = {"stufe1_sicher": 0, "stufe1_plausibel": 0, "unklar": 0, "stufe2": 0, "stufe3": 0,
            "verwaist_video": 0, "verwaist_audio": 0, "wellenform_nicht_anwendbar": 0}

    # ── Stufe 1
    kand, w1 = _tc_kandidaten(videos, audios)
    global_warn += w1
    kand_pro_video: dict[str, list[Kandidat]] = {}
    kand_pro_audio: dict[str, list[Kandidat]] = {}
    for k in kand:
        kand_pro_video.setdefault(k.video_id, []).append(k)
        kand_pro_audio.setdefault(k.audio_id, []).append(k)

    # Stark = Überlappung > 80 % der kürzeren Dauer (Offset eindeutig durch TC bestimmt).
    # Ein Audio darf STARK an mehreren Videos hängen (Ton läuft über zwei Takes durch, Multicam),
    # solange diese Videos sich zeitlich NICHT gegenseitig überlappen. Überlappen sich die
    # Videos (parallele Kameras / Uhren- oder Etikettierungsproblem) → Konflikt → unklar.
    def _stark(k: Kandidat) -> bool:
        return k.ueberlappung_ratio > UEBERLAPPUNG_SICHER

    def _video_ueberlappung(v1: AssetInfo, v2: AssetInfo) -> float:
        """Anteil der zeitlichen Überlappung zweier Videos an der kürzeren Dauer (0 = keine)."""
        if v1.tc_start_s is None or v2.tc_start_s is None:
            return 0.0
        ueb = min(v1.tc_ende_s, v2.tc_ende_s) - max(v1.tc_start_s, v2.tc_start_s)
        if ueb <= 2.0:
            return 0.0
        return ueb / (min(v1.dauer_s, v2.dauer_s) or 1.0)

    # Ein Audio deckt zwei Videos stark, die sich SELBST überlappen:
    #   ≥ MULTICAM_MIN_RATIO → parallel laufende Kameras (Multicam): Ton an BEIDE, je eigener Offset,
    #                          Status plausibel + Gruppe (kein Block, aber Blick drauf)
    #   sonst (5–50 %)       → Konflikt (Uhren-/Etikettierungsproblem?) → unklar mit Kandidaten
    konflikt_audios: set[str] = set()
    multicam_von_audio: dict[str, set[str]] = {}       # audio_id → beteiligte video_ids
    for aid, ks in kand_pro_audio.items():
        starke = [k for k in ks if _stark(k)]
        for i in range(len(starke)):
            for j in range(i + 1, len(starke)):
                r = _video_ueberlappung(v_by_id[starke[i].video_id], v_by_id[starke[j].video_id])
                if r >= MULTICAM_MIN_RATIO:
                    multicam_von_audio.setdefault(aid, set()).update({starke[i].video_id, starke[j].video_id})
                elif r > 0.05:
                    konflikt_audios.add(aid)
    stat.setdefault("multicam_gruppen", 0)
    stat["multicam_gruppen"] = len(multicam_von_audio)

    def _link_aus_kandidat(k: Kandidat, v: AssetInfo, status: str) -> tuple[LinkVorschlag, str, list[str]]:
        a = a_by_id[k.audio_id]
        if status == STATUS_SICHER:
            konf = min(0.99, 0.95 + 0.2 * (k.ueberlappung_ratio - UEBERLAPPUNG_SICHER))
        else:
            konf = 0.5 + 0.5 * k.ueberlappung_ratio
        link = LinkVorschlag(a.id, k.offset_s, METHODE_TIMECODE, round(konf, 3), k.begruendung,
                             kanal_fuer_transkription=a.record_kanal)
        nw = namens_warnungen(a, v)
        if a.dauer_s < 1.0:
            nw.append(f"Audio sehr kurz ({a.dauer_s:.1f} s) — vermutlich abgebrochene Aufnahme")
        andere = [k2 for k2 in kand_pro_audio.get(a.id, []) if k2.video_id != v.id and _stark(k2)]
        mc = multicam_von_audio.get(a.id)
        if mc and v.id in mc:
            partner = [v_by_id[x].dateiname for x in sorted(mc) if x != v.id]
            nw.append("Multicam: lief parallel zu " + ", ".join(partner)
                      + " — derselbe Ton ist an beide Kameras gebunden (je eigener Offset); bitte kurz gegenhören")
            status = STATUS_PLAUSIBEL
        elif andere:
            nw.append("Audio läuft über mehrere Video-Takes durch: zusätzlich "
                      + ", ".join(f"{v_by_id[k2.video_id].dateiname} ({k2.ueberlappung_ratio:.0%})" for k2 in andere))
        link.warnungen += nw
        if nw:
            link.begruendung += " Achtung: " + " ".join(nw)

        # ── Stufe 2 als Bestätigung (nur wenn Kamera-Scratch existiert)
        if v.scratch_kanal is None:
            link.begruendung += " Wellenform-Abgleich nicht anwendbar (kein Kamera-Scratch)."
            stat["wellenform_nicht_anwendbar"] += 1
        elif waveform_fn is not None:
            r = waveform_fn(a, v)
            if r is not None and getattr(r, "anwendbar", False) and getattr(r, "offset_s", None) is not None:
                stat["stufe2"] += 1
                delta = abs(r.offset_s - k.offset_s)
                if delta <= WELLENFORM_TOLERANZ_S:
                    link.begruendung += f" Wellenform bestätigt (Δ {delta*1000:.0f} ms)."
                    link.konfidenz = round(min(0.99, link.konfidenz + 0.02), 3)
                else:
                    link.warnungen.append(f"Wellenform widerspricht Timecode (Δ {delta:.2f} s)")
                    link.begruendung += f" Achtung: Wellenform widerspricht Timecode (Δ {delta:.2f} s)."
                    status = STATUS_PLAUSIBEL
                    link.konfidenz = round(min(link.konfidenz, 0.7), 3)
            elif r is not None:
                link.begruendung += f" Wellenform: {getattr(r, 'begruendung', 'kein Ergebnis')}."
        return link, status, list(a.warnungen) + nw

    for v in videos:
        cands = sorted(kand_pro_video.get(v.id, []), key=lambda k: (-k.ueberlappung_ratio, k.audio_id))
        if not cands:
            continue
        t = TakeVorschlag(v.id, STATUS_UNKLAR, v.namen.szene, v.namen.plan, v.namen.prise,
                          warnungen=list(v.warnungen))
        starke = [k for k in cands if _stark(k)]
        schwache = [k for k in cands if not _stark(k)]

        # Konflikt: ein starker Kandidat gehört zu einem Audio, das parallel laufende Videos trifft.
        konflikt = [k for k in starke if k.audio_id in konflikt_audios]
        if konflikt:
            alle = list(cands)
            for k in konflikt:
                for k2 in kand_pro_audio.get(k.audio_id, []):
                    if k2.video_id != v.id and k2 not in alle:
                        alle.append(k2)
            t.kandidaten = sorted(alle, key=lambda k: (k.video_id != v.id, -k.ueberlappung_ratio, k.audio_id))
            t.warnungen.append(
                "Mehrdeutig — Audio trifft zeitlich parallel laufende Videos: " + "; ".join(
                    f"{a_by_id[k.audio_id].dateiname} ↔ {v_by_id[k.video_id].dateiname} ({k.ueberlappung_ratio:.0%})"
                    for k in t.kandidaten)
                + " — nicht automatisch entschieden (zweite Kamera oder Uhren-/Etikettierungsproblem?)"
            )
            stat["unklar"] += 1
            verknuepft_video.add(v.id)
            takes.append(t)
            continue

        if starke:
            status = STATUS_SICHER
            for k in starke:
                link, st, w = _link_aus_kandidat(k, v, STATUS_SICHER)
                if st != STATUS_SICHER:
                    status = st
                t.links.append(link)
                t.warnungen += w
                verknuepft_audio.add(k.audio_id)
            stat["stufe1_sicher"] += 1
            for k in starke:
                mc = multicam_von_audio.get(k.audio_id)
                if mc and v.id in mc:
                    t.multicam_gruppe = "mc-" + "-".join(sorted(mc))[:60]
            if schwache:
                t.warnungen.append("Nur Randüberlappung (nicht verknüpft): " + "; ".join(
                    f"{a_by_id[k.audio_id].dateiname} ({k.ueberlappung_s:.1f} s, {k.ueberlappung_ratio:.0%})" for k in schwache))
            t.status = status
        else:
            # Nur schwache Kandidaten: genau einer, der sonst nirgends stark hängt → plausibel; sonst unklar.
            frei = [k for k in schwache if not any(_stark(k2) for k2 in kand_pro_audio.get(k.audio_id, []))]
            if len(frei) == 1:
                k = frei[0]
                link, st, w = _link_aus_kandidat(k, v, STATUS_PLAUSIBEL)
                t.links = [link]
                t.warnungen += w
                t.status = STATUS_PLAUSIBEL
                verknuepft_audio.add(k.audio_id)
                stat["stufe1_plausibel"] += 1
            elif len(frei) >= 2:
                t.kandidaten = frei
                t.warnungen.append("Mehrdeutig — mehrere Teil-Überlappungen: " + "; ".join(
                    f"{a_by_id[k.audio_id].dateiname} ({k.ueberlappung_ratio:.0%})" for k in frei)
                    + " — nicht automatisch entschieden")
                t.status = STATUS_UNKLAR
                stat["unklar"] += 1
            else:
                # Alle schwachen Kandidaten hängen stark an anderen Videos → dieses Video bleibt ohne Ton.
                t.warnungen.append("Nur Randüberlappung mit Audios, die zu anderen Takes gehören: " + "; ".join(
                    f"{a_by_id[k.audio_id].dateiname} ({k.ueberlappung_s:.1f} s)" for k in schwache))
                t.status = STATUS_VERWAIST
                stat["verwaist_video"] += 1
        verknuepft_video.add(v.id)
        takes.append(t)

    # ── Stufe 2/3 für Paare ohne TC-Entscheidung
    rest_videos = [v for v in videos if v.id not in verknuepft_video]
    rest_audios = [a for a in audios if a.id not in verknuepft_audio]
    for v in rest_videos:
        t = TakeVorschlag(v.id, STATUS_VERWAIST, v.namen.szene, v.namen.plan, v.namen.prise,
                          warnungen=list(v.warnungen))
        rang_v_, rang_a_ = drehtag_rang(videos, audios)
        pool = [a for a in rest_audios
                if (a.datum is None or v.datum is None
                    or (rang_v_.get(v.datum) is not None and rang_v_.get(v.datum) == rang_a_.get(a.datum)
                        if rang_v_ is not None else a.datum == v.datum))]
        # Namen NUR zum Eingrenzen des Kandidatenpools (nicht zum Entscheiden).
        if v.namen.szene is not None:
            eng = [a for a in pool if a.namen.szene == v.namen.szene
                   and (a.namen.plan is None or v.namen.plan is None or a.namen.plan == v.namen.plan)]
            if eng:
                pool = eng
        treffer: list[LinkVorschlag] = []
        if v.scratch_kanal is None:
            if pool and (waveform_fn or klappe_fn):
                t.warnungen.append("Wellenform-/Klappen-Abgleich nicht anwendbar: Video ohne Kamera-Scratch")
                stat["wellenform_nicht_anwendbar"] += 1
        else:
            for a in pool:
                if waveform_fn is not None:
                    r = waveform_fn(a, v)
                    if r is not None and getattr(r, "anwendbar", False) and getattr(r, "offset_s", None) is not None:
                        stat["stufe2"] += 1
                        lk = LinkVorschlag(a.id, _runde_ms(r.offset_s), METHODE_WAVEFORM,
                                           round(min(0.9, float(getattr(r, "konfidenz", 0.6))), 3),
                                           str(getattr(r, "begruendung", "Wellenform")),
                                           kanal_fuer_transkription=a.record_kanal)
                        treffer.append(lk)
                        continue
                if klappe_fn is not None:
                    kl = klappe_fn(a, v)
                    if kl is not None:
                        stat["stufe3"] += 1
                        off, grund = kl
                        treffer.append(LinkVorschlag(a.id, _runde_ms(off), METHODE_KLAPPE, KONFIDENZ_KLAPPE_MAX,
                                                     grund, kanal_fuer_transkription=a.record_kanal))
        if len(treffer) == 1:
            lk = treffer[0]
            a = a_by_id[lk.audio_id]
            nw = namens_warnungen(a, v)
            lk.warnungen += nw
            if nw:
                lk.begruendung += " Achtung: " + " ".join(nw)
            t.status = STATUS_PLAUSIBEL
            t.links = [lk]
            t.warnungen += list(a.warnungen) + nw
            verknuepft_audio.add(a.id)
        elif len(treffer) >= 2:
            t.status = STATUS_UNKLAR
            t.kandidaten = [Kandidat(lk.audio_id, v.id, lk.offset_s, 0.0, 0.0, lk.begruendung) for lk in treffer]
            t.warnungen.append("Mehrdeutig (Wellenform/Klappe): mehrere Audios passen — nicht automatisch entschieden")
            stat["unklar"] += 1
        else:
            if v.tc_start_s is None:
                t.warnungen.append(f"Video ohne verwertbaren Timecode (Quelle: {v.tc_quelle})")
            stat["verwaist_video"] += 1
        takes.append(t)

    # ── Audios ohne Verknüpfung → verwaist (bleiben sichtbar/auswählbar)
    for a in audios:
        if a.id in verknuepft_audio:
            continue
        w = list(a.warnungen) + namens_warnungen(a, None)
        if a.tc_start_s is None:
            w.append(f"Audio ohne verwertbaren Timecode (Quelle: {a.tc_quelle})")
        if a.id in kand_pro_audio:
            w.append("Kandidat eines mehrdeutigen Takes — manuell zuordnen")
        takes.append(TakeVorschlag(None, STATUS_VERWAIST, a.namen.szene, a.namen.plan, a.namen.prise,
                                   warnungen=w, audio_ids_verwaist=[a.id]))
        stat["verwaist_audio"] += 1

    for t in takes:
        t.warnungen = list(dict.fromkeys(t.warnungen))
        for lk in t.links:
            lk.warnungen = list(dict.fromkeys(lk.warnungen))
    return MatchErgebnis(takes, stat, global_warn)


# ─── Stufe 4 auf ausdrücklichen Wunsch ────────────────────────────────────

def matche_nach_dateiname(videos: list[AssetInfo], audios: list[AssetInfo]) -> list[LinkVorschlag | tuple[str, LinkVorschlag]]:
    """Explizit angeforderte Namens-Zuordnung (Szene/Plan/Take identisch).

    Liefert (video_id, LinkVorschlag) mit methode=dateiname, konfidenz ≤ 0,3, Offset 0 —
    der aufrufende Take wird `unklar` und muss manuell bestätigt werden.
    """
    out: list[tuple[str, LinkVorschlag]] = []
    for v in sorted(videos, key=lambda x: (x.dateiname, x.id)):
        if v.namen.leer:
            continue
        for a in sorted(audios, key=lambda x: (x.dateiname, x.id)):
            if (a.namen.szene, a.namen.plan, a.namen.prise) == (v.namen.szene, v.namen.plan, v.namen.prise):
                out.append((v.id, LinkVorschlag(
                    a.id, 0.0, METHODE_DATEINAME, KONFIDENZ_DATEINAME_MAX,
                    f"Dateiname: {a.dateiname} ↔ {v.dateiname} (Szene/Einstellung/Take identisch). "
                    "Offset unbekannt (0 s angenommen) — nur auf ausdrücklichen Wunsch, manuell prüfen.",
                    kanal_fuer_transkription=a.record_kanal,
                    warnungen=namens_warnungen(a, v),
                )))
    return out
