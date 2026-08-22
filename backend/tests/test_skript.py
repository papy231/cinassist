"""Kontext-Schicht — deterministische Teile: Drehbuch-Parser, Sprech-Klappe, Produktions-Sprech, Alignment-Heuristik."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.core.skript.parser import parse_text, parse_datei
from backend.core.skript.klappe import parse_klappe, klassifiziere_einheit, einheiten_aus_segmenten, analysiere_take
from backend.core.skript.alignment import lexikalisch, _schwelle_fuer, SkriptZeileRef

# Pfad zum Referenz-Drehbuch. Über CINASSIST_SKRIPT_PDF setzbar, sonst wird der Test
# übersprungen. Das Drehbuch gehört zum Testbestand und liegt nicht im Repository.
SKRIPT_PDF = Path(os.environ.get("CINASSIST_SKRIPT_PDF", "testdaten/Full_Script_-_Pinky_Promise.pdf"))

MINI = """SHORTCUT WS 23/24
“PINKY PROMISE”

FADE IN:

1. INT. ORPHEUS’S LIVING ROOM – MORNING

First we are shown 3 elements of the interior: a flower, a
couple of tea cups and some photos.

CUT TO:

2. INT. ORPHEUS’S CORRIDOR – DAY

The door opens. Fred was talking since the door opened:

FRED (to Orpheus)
“I’m telling you, when the album is out, we’re gonna be
huge!”

Orpheus looks unphased.

ORPHEUS (to Fred)
“I wan’t to be alone now”

THE END
"""


def test_parser_mini():
    sk = parse_text(MINI)
    assert sk.titel == "PINKY PROMISE"
    assert [s.nummer for s in sk.szenen] == ["1", "2"]
    s1, s2 = sk.szenen
    assert s1.innen_aussen == "INT" and s1.ort == "ORPHEUS’S LIVING ROOM" and s1.tageszeit == "MORNING"
    assert [z.art for z in s1.zeilen] == ["aktion", "uebergang"]
    dial = [z for z in s2.zeilen if z.art == "dialog"]
    assert [(z.figur, z.regie) for z in dial] == [("FRED", "to Orpheus"), ("ORPHEUS", "to Fred")]
    assert dial[0].text == "I’m telling you, when the album is out, we’re gonna be huge!"   # Zeilenumbruch + Anführungszeichen weg
    assert s2.figuren == ["FRED", "ORPHEUS"]
    assert s2.zeilen[-1].art == "uebergang" and s2.zeilen[-1].text == "THE END"


def test_parser_seitenumbruch_kein_absatzende():
    t = "1. INT. RAUM – TAG\n\nEr geht zur Tür und schaut\n\fzurück in den Raum.\n\nCUT TO:\n"
    sk = parse_text(t)
    assert sk.szenen[0].zeilen[0].text == "Er geht zur Tür und schaut zurück in den Raum."


@pytest.mark.skipif(not SKRIPT_PDF.exists(), reason="Pinky-Promise-Skript nicht vorhanden")
def test_parser_echtes_skript():
    sk = parse_datei(SKRIPT_PDF)
    assert sk.titel == "PINKY PROMISE" and len(sk.szenen) == 5
    dial = {s.nummer: [z for z in s.zeilen if z.art == "dialog"] for s in sk.szenen}
    assert len(dial["1"]) == 0 and len(dial["2"]) == 4 and len(dial["3"]) == 7 and len(dial["4"]) == 1 and len(dial["5"]) == 12
    assert dial["5"][0].figur == "FRED" and dial["5"][0].text.startswith("Orpheus, are you there?")
    assert all(s.ort for s in sk.szenen)


@pytest.mark.parametrize("text, szene, take", [
    ("Scene 2.1, Take 3.", "2.1", 3),
    ("Szene 3.2, Teil 3. Set.", "3.2", 3),
    ("In 5.1.1. Take 3.", "5.1.1", 3),
    ("Sie in 5.2.1, Take 2. Set.", "5.2.1", 2),
    ("Scene 4.1, Day 2. Set.", "4.1", 2),
    ("2.1, Date 2.", "2.1", 2),
    ("Mal andere. Scene 5, 5.2.2, Take 1. Set.", "5.2.2", 1),
    ("Kameraloi, Scene 5.1.1, Take 2. Set.", "5.1.1", 2),
    ("Scene 2, Take 1", "2", 1),
])
def test_klappe(text, szene, take):
    k = parse_klappe(text)
    assert (k.szene, k.take, k.quelle) == (szene, take, "audio")


def test_klappe_kein_treffer():
    assert parse_klappe("Babe, musst du nicht langsam aufwachen?").quelle == "keine"
    assert parse_klappe("").quelle == "keine"


@pytest.mark.parametrize("text, art", [
    ("Kamera läuft.", "produktion"), ("Set.", "produktion"), ("Bitte schön.", "produktion"), ("Danke.", "produktion"),
    ("Können wir den direkt nochmal machen?", "produktion"), ("Mach nochmal auf Anfang, einmal.", "produktion"),
    ("Babe, musst du nicht langsam aufwachen?", "spiel"), ("Komm, verarsch mich nicht.", "spiel"), ("Scene 2.1, Take 3.", "slate"),
])
def test_klassifikation(text, art):
    assert klassifiziere_einheit(text) == art


def test_einheiten_split_und_nummern():
    segs = [{"start": 0.0, "end": 6.0, "text": "Scene 2.1, Take 3. Babe? Musst du nicht aufwachen? Ich geh Tee machen.", "sprecher": "SPEAKER_00",
             "woerter": [{"wort": w, "start": 0.3 * i, "end": 0.3 * i + 0.25} for i, w in enumerate(
                 ["Scene", "2.1,", "Take", "3.", "Babe?", "Musst", "du", "nicht", "aufwachen?", "Ich", "geh", "Tee", "machen."])]}]
    e = einheiten_aus_segmenten(segs)
    # „Babe?“ (reine Anrede) wird mit dem Folgesatz verschmolzen — eine Skriptzeile, eine Einheit
    assert [x.text for x in e] == ["Scene 2.1, Take 3.", "Babe? Musst du nicht aufwachen?", "Ich geh Tee machen."]
    assert e[0].art == "slate" and e[1].art == "spiel"
    b = analysiere_take(segs, 120.0)
    assert b.klappe.szene == "2.1" and b.klappe.take == 3 and b.spiel_start is not None and b.spiel_start > 1.0


def test_bitte_im_spiel_bleibt_spiel():
    segs = [{"start": 0, "end": 2, "text": "Scene 2.1, Take 3."}, {"start": 3, "end": 4, "text": "Bitte."},
            {"start": 10, "end": 14, "text": "Babe, musst du nicht aufwachen?"}, {"start": 20, "end": 21, "text": "Bitte."},
            {"start": 22, "end": 23, "text": "Bitte."}, {"start": 60, "end": 61, "text": "Danke."}]
    b = analysiere_take(segs, 62.0)
    arten = [(e.text, e.art) for e in b.einheiten]
    assert ("Bitte.", "produktion") in arten[:2]            # vor dem Spiel: Set-Kommando
    assert arten[3] == ("Bitte.", "spiel") and arten[4] == ("Bitte.", "spiel")   # im Spiel: Text
    assert arten[-1] == ("Danke.", "produktion")            # am Ende: Regie-Cut


def test_abbruch_erkannt():
    segs = [{"start": 0, "end": 2, "text": "Scene 2.2, Take 2."}, {"start": 5, "end": 8, "text": "Babe, musst du nicht aufwachen?"},
            {"start": 9, "end": 12, "text": "Können wir den direkt nochmal machen?"}, {"start": 13, "end": 14, "text": "Sorry."}]
    b = analysiere_take(segs, 20.0)
    assert b.ng["abbruch"] is True and b.ng["kurz"] is True


def test_lexikalisch_und_schwellen():
    assert lexikalisch("Babe, musst du nicht langsam aufwachen?", "Babe, es ist Zeit aufzustehen, musst du nicht aufwachen?") > 0.45
    assert lexikalisch("Wir brauchen dich.", "Das Wetter ist schön.") < 0.25
    assert _schwelle_fuer("Babe.", 0.55) == 0.72 and _schwelle_fuer("Komm raus.", 0.55) == 0.66 and _schwelle_fuer("Ich weiß, dass du da bist", 0.55) == 0.55


@pytest.mark.skipif(os.getenv("OLLAMA_TESTS") != "1", reason="braucht Ollama + bge-m3 (OLLAMA_TESTS=1)")
def test_alignment_crosslingual():
    from backend.core.skript.alignment import aligne
    zeilen = [SkriptZeileRef("a", 0, "FRED", "I’m telling you, when the album is out, we’re gonna be huge!"),
              SkriptZeileRef("b", 1, "FRED", "We need you man, now more then ever…"),
              SkriptZeileRef("c", 2, "ORPHEUS", "But it was my fault…")]
    r = aligne(["Wenn das Album raus ist, werden wir richtig berühmt sein.", "Wir brauchen dich.", "Jetzt noch mehr als zuvor.", "Aber es war meine Schuld."], zeilen)
    assert r.abdeckung == 1.0 and [z.zeile_nr for z in r.zuordnungen] == [0, 1, 1, 2]
