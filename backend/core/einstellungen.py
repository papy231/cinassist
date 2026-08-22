"""Projekt-Einstellungen (JSON-Datei unter DATA_DIR) — bewusst ohne DB-Migration.

Aktuell: Transkription (Whisper) — Sprache, Glossar (Namen/Begriffe als initial_prompt),
Modellqualität, Kanalwahl beim verknüpften Ton.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.core.config import DATA_DIR

_DATEI = DATA_DIR / "einstellungen.json"
_LOCK = threading.Lock()

STANDARD = {
    "transkription": {
        "sprache": "de",              # ISO-639-1 oder "auto"
        "glossar": [],                # Namen/Begriffe → Whisper initial_prompt
        "modell": "turbo",            # turbo | qualitaet
        "kanal": "sprachreichster",   # sprachreichster | record
    },
    "projekt": {
        # Freitext-Kontext für den Clip-Bericht (Synthese): Projektart, Titel, Figuren, Drehsituation.
        # Wird dem LLM als FAKT mitgegeben — z. B. „Kurzfilm ‚Pinky Promise‘, Dailies vom Dreh; Figuren: Yuri, Babe“.
        "kontext": "",
        # Obergrenze Sprecher je Clip für die Diarization (None = frei). Bei Dialogszenen mit 2 Figuren: 2.
        "max_sprecher": None,
    },
}

MODELLE = {
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "qualitaet": "mlx-community/whisper-large-v3-mlx",
}


def lade() -> dict:
    with _LOCK:
        try:
            daten = json.loads(_DATEI.read_text("utf-8"))
        except (OSError, ValueError):
            daten = {}
    out = json.loads(json.dumps(STANDARD))
    for k, v in (daten or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def speichere(neu: dict) -> dict:
    akt = lade()
    for k, v in neu.items():
        if isinstance(v, dict) and isinstance(akt.get(k), dict):
            akt[k].update(v)
        else:
            akt[k] = v
    with _LOCK:
        _DATEI.parent.mkdir(parents=True, exist_ok=True)
        _DATEI.write_text(json.dumps(akt, ensure_ascii=False, indent=2), "utf-8")
    return akt


def projekt() -> dict:
    return lade()["projekt"]


def projekt_kontext() -> str:
    return str(projekt().get("kontext") or "").strip()


def transkription() -> dict:
    t = lade()["transkription"]
    t["glossar"] = [g.strip() for g in (t.get("glossar") or []) if g and g.strip()]
    return t


def whisper_repo() -> str:
    from backend.core.config import WHISPER_MODEL
    t = transkription()
    if t.get("modell") == "qualitaet":
        return MODELLE["qualitaet"]
    return WHISPER_MODEL or MODELLE["turbo"]


def initial_prompt() -> str | None:
    g = transkription().get("glossar") or []
    if not g:
        return None
    # Neutrale, kommaseparierte Liste. (Getestet: dialogartige Prompts mit Ausrufen destabilisieren
    # Whisper — Sprachkipp ins Englische, Wiederholungen. Die Schreibweise der Namen wird zusätzlich
    # deterministisch nachgezogen, siehe `glossar_angleichen`.)
    return ", ".join(g[:60]) + "."


_PHON = [("sch", "s"), ("ph", "f"), ("th", "t"), ("ck", "k"), ("ie", "i"), ("y", "j"), ("c", "k"), ("z", "s"), ("v", "f"), ("w", "v"), ("ä", "e"), ("ö", "o"), ("ü", "u"), ("ß", "s")]


def _phon(wort: str) -> str:
    import re
    w = re.sub(r"[^a-zäöüß]", "", wort.lower())
    for a, b in _PHON:
        w = w.replace(a, b)
    # Doppelbuchstaben zusammenziehen
    return re.sub(r"(.)\1+", r"\1", w)


def glossar_angleichen(wort: str) -> str:
    """Schreibt ein transkribiertes Wort auf die Glossar-Schreibweise um, wenn es phonetisch gleich ist
    („Juri“ → „Yuri“). Satzzeichen bleiben erhalten; nur einzelne Wörter, nur Glossar-Treffer."""
    import re
    g = transkription().get("glossar") or []
    if not g or not wort:
        return wort
    m = re.match(r"^(\W*)(.*?)(\W*)$", wort, re.UNICODE)
    if not m:
        return wort
    pre, kern, post = m.groups()
    if not kern:
        return wort
    ph = _phon(kern)
    for term in g:
        if " " in term:
            continue
        if _phon(term) == ph and kern != term:
            # Groß-/Kleinschreibung des Glossars übernehmen
            return f"{pre}{term}{post}"
    return wort
