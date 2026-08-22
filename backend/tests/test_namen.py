from datetime import date

from backend.core.sync.namen import (parse_audio_name, parse_ordner_datum, parse_tape_datum, parse_video_name)


def test_video_namen():
    n = parse_video_name("PPRM23_S004_S003_T001.MOV")
    assert (n.szene, n.plan, n.prise) == (4, 3, 1)
    assert parse_video_name("irgendwas.mov").leer


def test_audio_namen_mit_ixml_vorrang():
    n = parse_audio_name("+SZENE4-3-002.WAV", ixml_scene="+SZENE4-3", ixml_take="002")
    assert (n.szene, n.plan, n.prise, n.unbekannte_markierung) == (4, 3, 2, "+")
    n = parse_audio_name("SZENE4-006.WAV", ixml_scene="SZENE4", ixml_take="006")
    assert (n.szene, n.plan, n.prise, n.unbekannte_markierung) == (4, None, 6, None)
    # nur Dateiname
    n = parse_audio_name("SZENE4-4-001.WAV")
    assert (n.szene, n.plan, n.prise) == (4, 4, 1)
    # Markierung nur im Dateinamen, SCENE-Feld ohne
    n = parse_audio_name("+SZENE7-1-003.WAV", ixml_scene="SZENE7-1", ixml_take="003")
    assert n.unbekannte_markierung == "+"


def test_datum():
    assert parse_tape_datum("231117") == date(2023, 11, 17)
    assert parse_ordner_datum("11-17-23") == date(2023, 11, 17)
    assert parse_ordner_datum("2023-11-17") == date(2023, 11, 17)
    assert parse_tape_datum("xx") is None
