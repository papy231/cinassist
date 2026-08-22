"""Deterministische Teile der Clip-Analyse: Entspekulieren, Glossar-Angleichung, Stichproben, Namens-Nachprüfung."""
from __future__ import annotations

import pytest

from backend.core import einstellungen as E
from backend.core.vision_describe import entspekulieren
from backend.workers.ingest import _stichproben_fraktionen


@pytest.mark.parametrize("roh, erwartet", [
    ("The image shows two people sitting on a couch. They appear to be engaged in conversation or an activity together.",
     "The image shows two people sitting on a couch."),
    ("She appears to be enjoying her time as she looks out the window.", ""),
    ("The image shows an empty pill bottle, possibly containing medication, on a wooden table.",
     "The image shows an empty pill bottle."),
    ("There are two remote controls on the floor, suggesting that they might have been watching television.",
     "There are two remote controls on the floor."),
    ("A woman is holding a cup. A potted plant stands near the wall, adding some greenery to space.",
     "A woman is holding a cup. A potted plant stands near the wall."),
    # Beobachtungen bleiben unangetastet
    ("One person appears to be holding another person in their arms.",
     "One person appears to be holding another person in their arms."),
])
def test_entspekulieren(roh, erwartet):
    assert entspekulieren(roh) == erwartet


def test_entspekulieren_ergaenzt_nie(monkeypatch):
    roh = "The room has a casual, lived-in feel, with personal belongings scattered around."
    out = entspekulieren(roh)
    assert out == "" or all(w in roh for w in out.rstrip(".").split())


def test_glossar_angleichen(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "_DATEI", tmp_path / "e.json")
    E.speichere({"transkription": {"glossar": ["Yuri", "Babe", "Pinky Promise"]}})
    assert E.glossar_angleichen("Juri.") == "Yuri."
    assert E.glossar_angleichen("juri,") == "Yuri,"
    assert E.glossar_angleichen("Yuri") == "Yuri"
    assert E.glossar_angleichen("Baabe,") == "Babe,"   # Doppelvokal zusammengezogen
    assert E.glossar_angleichen("Tee") == "Tee"          # kein Glossar-Treffer → unverändert
    assert E.glossar_angleichen("Hallo") == "Hallo"
    assert E.initial_prompt() == "Yuri, Babe, Pinky Promise."


def test_glossar_leer_laesst_alles(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "_DATEI", tmp_path / "e.json")
    E.speichere({"transkription": {"glossar": []}})
    assert E.glossar_angleichen("Juri") == "Juri"
    assert not E.initial_prompt()


def test_stichproben_fraktionen():
    # Standard: alle 30 s ein Frame, 1..12, gleichmäßig verteilt, nie am Rand
    assert _stichproben_fraktionen(3.0) == [0.5]
    assert _stichproben_fraktionen(20.0) == [0.5]
    fr = _stichproben_fraktionen(60.0)
    assert fr == [0.25, 0.75]
    fr = _stichproben_fraktionen(240.0)           # 4 min → 8 Frames
    assert len(fr) == 8 and 0 < fr[0] < fr[-1] < 1
    assert len(_stichproben_fraktionen(3600.0)) == 12   # Deckel
