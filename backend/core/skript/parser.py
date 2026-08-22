"""Drehbuch-Parser — PDF/TXT/Fountain-ähnlich → Szenen + Zeilen (Dialog / Aktion / Übergang).

Tolerant gegenüber dem, was Studentenfilme liefern (getestet am „Pinky Promise“-Skript):
  * Szenenkopf:   `1. INT. ORPHEUS’S LIVING ROOM – MORNING`   oder   `INT. KÜCHE - TAG`   oder   `SZENE 3 …`
  * Figur-Cue:    `ORPHEUS (to Eurydice)`  — Großbuchstaben-Zeile, optional Klammer; Dialogtext in den Folgezeilen,
                  oft in „Anführungszeichen“, bis zur Leerzeile.
  * Übergang:     `CUT TO:` / `FADE IN:` / `FADE OUT.` / `SCHNITT AUF:` → eigene Zeile (art=uebergang), nicht Aktion.
  * Alles andere: Aktion / Regieanweisung (Absätze zwischen Leerzeilen zusammengezogen).
Rein deterministisch. Kein LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SZENE_RE = re.compile(
    r"^\s*(?:(?P<nr>\d+[A-Za-z]?)[.)]\s+)?(?P<ie>INT\.?/EXT\.?|EXT\.?/INT\.?|INT\.?|EXT\.?|I/E\.?)\s+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
_SZENE_DE_RE = re.compile(r"^\s*(?:SZENE|SCENE)\s+(?P<nr>\d+[A-Za-z]?)\b[:.\s-]*(?P<rest>.*)$", re.IGNORECASE)
_UEBERGANG_RE = re.compile(r"^\s*(FADE IN|FADE OUT|FADE TO BLACK|CUT TO|SMASH CUT TO|DISSOLVE TO|MATCH CUT TO|SCHNITT AUF|ABBLENDE|AUFBLENDE|THE END|ENDE)\s*[:.]?\s*$", re.IGNORECASE)
_CUE_RE = re.compile(r"^\s*(?P<figur>[A-ZÄÖÜ][A-ZÄÖÜ0-9 .'’\-]{1,40}?)\s*(?P<regie>\([^)]*\))?\s*(?:\(CONT'D\)|\(V\.O\.\)|\(O\.S\.\))?\s*$")
_PAGE_RE = re.compile(r"^\s*\d+\.?\s*$")
_TITEL_RE = re.compile(r"^\s*[“\"„]?(?P<t>[A-ZÄÖÜ][^”\"“]{2,80})[”\"“]?\s*$")


@dataclass
class Zeile:
    art: str                      # dialog | aktion | uebergang
    text: str
    figur: str | None = None
    regie: str | None = None


@dataclass
class SzeneRoh:
    nummer: str
    ueberschrift: str
    innen_aussen: str | None
    ort: str | None
    tageszeit: str | None
    zeilen: list[Zeile] = field(default_factory=list)

    @property
    def figuren(self) -> list[str]:
        out: list[str] = []
        for z in self.zeilen:
            if z.art == "dialog" and z.figur and z.figur not in out:
                out.append(z.figur)
        return out


@dataclass
class SkriptRoh:
    titel: str | None
    szenen: list[SzeneRoh]
    roh_text: str


def _ist_cue(zeile: str, m: "re.Match[str]") -> bool:
    """Figur-Cue: Figurname komplett in Großbuchstaben (Klammer darf klein sein), kurz, kein Satzende."""
    figur = m.group("figur").strip()
    if figur != figur.upper() or len(figur.split()) > 4:
        return False
    if zeile.rstrip().endswith((".", "!", "?", ",", ":")) and not m.group("regie"):
        return False
    return True


def _split_kopf(ie: str, rest: str) -> tuple[str | None, str | None]:
    """`ORPHEUS’S LIVING ROOM – MORNING` → (ort, tageszeit)."""
    teile = re.split(r"\s+[–—-]+\s+", rest, maxsplit=1)
    ort = teile[0].strip(" .") or None
    zeit = teile[1].strip(" .") if len(teile) > 1 else None
    return ort, zeit


def _bereinige(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^[“\"„]\s*", "", t)
    t = re.sub(r"\s*[”\"“]$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_text(text: str) -> SkriptRoh:
    # Seitenumbruch (\f) ist KEIN Absatzende — pypdf liefert "…final look at\n\fOrpheus, before…".
    zeilen_roh = [ln.rstrip("\r") for ln in text.replace("\f", "").split("\n")]
    titel: str | None = None
    szenen: list[SzeneRoh] = []
    akt: SzeneRoh | None = None
    i = 0
    n = len(zeilen_roh)
    # Titel: erste „…“-Zeile vor der ersten Szene
    for ln in zeilen_roh[:15]:
        m = _TITEL_RE.match(ln)
        if m and ("“" in ln or '"' in ln) and not _UEBERGANG_RE.match(ln):
            titel = m.group("t").strip()
            break

    def neue_szene(nr: str | None, ie: str | None, rest: str, ueberschrift: str) -> SzeneRoh:
        ort, zeit = _split_kopf(ie or "", rest)
        nummer = nr or str(len(szenen) + 1)
        sz = SzeneRoh(nummer=nummer, ueberschrift=ueberschrift.strip(),
                      innen_aussen=(ie or "").upper().rstrip(".") or None, ort=ort, tageszeit=zeit)
        szenen.append(sz)
        return sz

    while i < n:
        ln = zeilen_roh[i]
        s = ln.strip()
        if not s or _PAGE_RE.match(s):
            i += 1
            continue
        m = _SZENE_RE.match(s)
        if m:
            akt = neue_szene(m.group("nr"), m.group("ie"), m.group("rest"), s)
            i += 1
            continue
        m = _SZENE_DE_RE.match(s)
        if m and (akt is None or m.group("nr") != akt.nummer):
            akt = neue_szene(m.group("nr"), None, m.group("rest"), s)
            i += 1
            continue
        if _UEBERGANG_RE.match(s):
            if akt is not None:
                akt.zeilen.append(Zeile(art="uebergang", text=s.rstrip(":.").upper()))
            i += 1
            continue
        if akt is None:
            i += 1          # Kopfzeilen vor der ersten Szene (Titel, Kurs …)
            continue
        mc = _CUE_RE.match(s)
        if mc and _ist_cue(s, mc):
            # Figur-Cue → Dialog bis Leerzeile
            figur = mc.group("figur").strip(" .'’")
            regie = mc.group("regie")
            j = i + 1
            buf: list[str] = []
            while j < n and zeilen_roh[j].strip():
                t = zeilen_roh[j].strip()
                # Ein Dialog endet an der Leerzeile, am Szenenkopf, am Übergang — und am
                # nächsten Figur-Cue. Der letzte Fall ist entscheidend für PDFs, die ohne
                # Leerzeilen extrahiert werden: ohne ihn verschlingt die erste Replik einer
                # Szene alle folgenden Figuren und Regieanweisungen, und die Szene behält
                # nur eine einzige, unbrauchbar lange Zeile.
                mc_next = _CUE_RE.match(t)
                if _SZENE_RE.match(t) or _UEBERGANG_RE.match(t) or (mc_next and _ist_cue(t, mc_next)):
                    break
                if _PAGE_RE.match(t):
                    j += 1
                    continue
                buf.append(t)
                j += 1
            if buf:
                akt.zeilen.append(Zeile(art="dialog", text=_bereinige(" ".join(buf)), figur=figur,
                                        regie=regie.strip("()") if regie else None))
            i = j
            continue
        # Aktion: Absatz bis Leerzeile
        j = i
        buf = []
        while j < n and zeilen_roh[j].strip():
            t = zeilen_roh[j].strip()
            mc2 = _CUE_RE.match(t)
            if _SZENE_RE.match(t) or _UEBERGANG_RE.match(t) or (mc2 and _ist_cue(t, mc2)):
                break
            if _PAGE_RE.match(t):
                j += 1
                continue
            buf.append(t)
            j += 1
        if buf:
            akt.zeilen.append(Zeile(art="aktion", text=_bereinige(" ".join(buf))))
        i = max(j, i + 1)
    return SkriptRoh(titel=titel, szenen=szenen, roh_text=text)


def lese_datei(pfad: str | Path) -> str:
    p = Path(pfad)
    if p.suffix.lower() == ".pdf":
        import pypdf
        r = pypdf.PdfReader(str(p))
        return "\n\f".join((pg.extract_text() or "") for pg in r.pages)
    return p.read_text("utf-8", errors="replace")


def parse_datei(pfad: str | Path) -> SkriptRoh:
    return parse_text(lese_datei(pfad))
