from fractions import Fraction

from backend.core.sync.bwf_ixml import lese_wav, sekunden_zu_tc, tc_zu_sekunden
from backend.tests.conftest import AUDIO_DIR, korpus
from backend.tests.helfer import schreibe_bwf_wav


def test_tc_roundtrip():
    assert sekunden_zu_tc(46621.583, Fraction(24)) == "12:57:01:14"
    assert abs(tc_zu_sekunden("12:57:04:07", Fraction(24)) - (12 * 3600 + 57 * 60 + 4 + 7 / 24)) < 1e-9


def test_synthetisches_bwf(tmp_path):
    p = schreibe_bwf_wav(tmp_path / "SZENE9-2-003.WAV", kanaele=3, dauer_s=1.5,
                         time_reference=2237836001, scene="+SZENE9-2", take="003", tracks=["Record", "Safety", "DPA"])
    w = lese_wav(p)
    assert (w.sample_rate, w.kanaele, w.bits) == (48000, 3, 16)
    assert abs(w.dauer_s - 1.5) < 1e-6
    assert w.bext.time_reference == 2237836001
    assert w.ixml.scene == "+SZENE9-2" and w.ixml.take == "003" and w.ixml.tape == "231117"
    assert w.ixml.track_index_by_name("record") == 0
    assert w.tc_quelle == "bwf"
    assert w.tc_start_str() == "12:57:01:14"


def test_bext_ixml_divergenz_ergibt_keine(tmp_path):
    p = schreibe_bwf_wav(tmp_path / "x.WAV", time_reference=48000 * 100, ixml_ts_lo=48000 * 200)
    w = lese_wav(p)
    assert w.tc_start_seconds is None
    assert w.tc_quelle == "keine"
    assert any("≠" in x for x in w.warnungen)


def test_kein_wav(tmp_path):
    p = tmp_path / "nix.wav"
    p.write_bytes(b"hello world, definitely not riff")
    try:
        lese_wav(p)
    except ValueError as e:
        assert "Keine WAV" in str(e)
    else:
        raise AssertionError("ValueError erwartet")


@korpus
def test_korpus_szene4_3_002():
    w = lese_wav(AUDIO_DIR / "+SZENE4-3-002.WAV")
    assert (w.sample_rate, w.kanaele, w.bits) == (48000, 6, 24)
    assert w.bext.time_reference == 2237836001
    assert w.ixml.scene == "+SZENE4-3" and w.ixml.take == "002" and w.ixml.tape == "231117"
    assert w.ixml.circled is False
    assert w.ixml.timecode_rate == "24/1" and w.ixml.timecode_flag == "NDF"
    assert [t.name for t in w.ixml.tracks] == ["Record", "Safety", "ANGEL", "DPA", "DPA2", "KM185"]
    assert w.ixml.track_index_by_name("Record") == 0
    assert w.tc_start_str() == "12:57:01:14"
    assert abs(w.dauer_s - 81.9236) < 0.01
