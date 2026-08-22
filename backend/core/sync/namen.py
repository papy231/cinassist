"""Dateinamen-Parser (Stufe 4) — Szene / Einstellung (Plan) / Take.

Dient NUR zum Gruppieren der Anzeige und zum Erzeugen von Warnungen. Der Name
entscheidet nie allein über eine Zuordnung (Auftrag, Regel 3: auf dem Korpus sind
Audio-Take 002 ↔ Video T001 systematisch um eins verschoben).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class NamensTeile:
    szene: Optional[int]
    plan: Optional[int]
    prise: Optional[int]
    unbekannte_markierung: Optional[str] = None   # z. B. "+" — Bedeutung unbekannt, nie interpretieren

    @property
    def leer(self) -> bool:
        return self.szene is None and self.plan is None and self.prise is None


# PPRM23_S004_S003_T001  →  Szene 4 / Einstellung 3 / Take 1
_RE_VIDEO = re.compile(r"S(?P<szene>\d{1,3})_S(?P<plan>\d{1,3})_T(?P<prise>\d{1,3})", re.IGNORECASE)
# +SZENE4-3-002  /  SZENE4-002  /  SZENE4-3
_RE_AUDIO = re.compile(
    r"^(?P<mark>[+*#!]?)\s*SZENE\s*(?P<szene>\d{1,3})(?:-(?P<plan>\d{1,3}))?(?:-(?P<prise>\d{1,3}))?$",
    re.IGNORECASE,
)
# Generische Fallbacks: "S4P3T2", "Sc4_Sh3_Tk2", "4-3-2"
_RE_GENERISCH = re.compile(
    r"(?:sc(?:ene)?|s|szene)\s*_?(?P<szene>\d{1,3})[^0-9]+(?:sh(?:ot)?|p|plan|s)\s*_?(?P<plan>\d{1,3})[^0-9]+(?:t(?:ake|k)?)\s*_?(?P<prise>\d{1,3})",
    re.IGNORECASE,
)


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]+$", "", name.strip())


def parse_video_name(dateiname: str) -> NamensTeile:
    m = _RE_VIDEO.search(_stem(dateiname))
    if m:
        return NamensTeile(int(m["szene"]), int(m["plan"]), int(m["prise"]))
    m = _RE_GENERISCH.search(_stem(dateiname))
    if m:
        return NamensTeile(int(m["szene"]), int(m["plan"]), int(m["prise"]))
    return NamensTeile(None, None, None)


def parse_audio_name(dateiname: str, ixml_scene: Optional[str] = None,
                     ixml_take: Optional[str] = None) -> NamensTeile:
    """iXML SCENE/TAKE haben Vorrang (vom Tonmeister am Gerät eingegeben); Fallback Dateiname."""
    mark: Optional[str] = None
    szene = plan = prise = None
    quelle = None
    if ixml_scene:
        m = _RE_AUDIO.match(ixml_scene.strip())
        if m:
            quelle = m
        else:
            # SCENE ohne Take-Suffix, aber evtl. mit Markierung: "+SZENE4-3"
            m2 = re.match(r"^(?P<mark>[+*#!]?)\s*(?P<rest>.*)$", ixml_scene.strip())
            if m2 and m2["mark"]:
                mark = m2["mark"]
    if quelle is None:
        quelle = _RE_AUDIO.match(_stem(dateiname))
    if quelle:
        mark = quelle["mark"] or mark or None
        szene = int(quelle["szene"])
        plan = int(quelle["plan"]) if quelle["plan"] else None
        prise = int(quelle["prise"]) if quelle["prise"] else None
    if prise is None and ixml_take and ixml_take.strip().isdigit():
        prise = int(ixml_take.strip())
    if szene is None:
        m = _RE_GENERISCH.search(_stem(dateiname))
        if m:
            szene, plan, prise = int(m["szene"]), int(m["plan"]), int(m["prise"])
    # "SZENE4-3-002" im iXML-SCENE-Feld ohne Take-Suffix, Dateiname mit "+": Markierung aus Dateiname.
    if mark is None:
        m3 = _RE_AUDIO.match(_stem(dateiname))
        if m3 and m3["mark"]:
            mark = m3["mark"]
    return NamensTeile(szene, plan, prise, mark or None)


# ─── Datums-Ableitung für Gruppierung ─────────────────────────────────────

def parse_tape_datum(tape: Optional[str]) -> Optional[date]:
    """iXML TAPE '231117' → 2023-11-17 (Sound-Devices-Konvention YYMMDD)."""
    if not tape:
        return None
    t = tape.strip()
    if re.fullmatch(r"\d{6}", t):
        yy, mm, dd = int(t[0:2]), int(t[2:4]), int(t[4:6])
        try:
            return date(2000 + yy, mm, dd)
        except ValueError:
            return None
    if re.fullmatch(r"\d{8}", t):
        try:
            return date(int(t[0:4]), int(t[4:6]), int(t[6:8]))
        except ValueError:
            return None
    return None


def parse_ordner_datum(ordnername: Optional[str]) -> Optional[date]:
    """Unterordner '11-17-23' (MM-DD-YY, MixPre-Standard) oder '2023-11-17'."""
    if not ordnername:
        return None
    n = ordnername.strip()
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})", n)
    if m:
        mm, dd, yy = (int(g) for g in m.groups())
        try:
            return date(2000 + yy, mm, dd)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", n)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def parse_datum_aus_dateiname(dateiname: str) -> Optional[date]:
    """'…_2023-11-17_…' oder '…_20231117_…' im Dateinamen."""
    s = _stem(dateiname)
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None
