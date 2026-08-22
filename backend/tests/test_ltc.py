import numpy as np
import pytest

from backend.core.sync.ltc import decode_ltc, finde_ltc_in_video, kanal_statistik
from backend.tests.conftest import VIDEO_DIR, korpus
from backend.tests.helfer import erzeuge_ltc_signal

ERWARTET = {
    "PPRM23_S004_S003_T001.MOV": "12:57:04:07",
    "PPRM23_S004_S003_T002.MOV": "13:00:46:12",
    "PPRM23_S004_S003_T003.MOV": "13:09:27:01",
    "PPRM23_S004_S003_T004.MOV": "13:12:06:15",
    "PPRM23_S004_S003_T005.MOV": "13:15:04:01",
    "PPRM23_S004_S003_T006.MOV": "13:30:25:15",
}


@pytest.mark.parametrize("fps", [24, 25, 30])
def test_synthetisch_roundtrip(fps):
    x = erzeuge_ltc_signal("13:00:46:12", fps, sekunden=4.0)
    e = decode_ltc(x, 48000)
    assert e.gueltig, e.warnungen
    assert e.fps == fps
    assert e.tc_start == "13:00:46:12"
    assert e.kontinuitaet >= 0.95


def test_synthetisch_start_mit_versatz():
    """LTC beginnt erst 1,0 s nach Signalanfang → Start-TC wird um die Vorlaufzeit zurückgerechnet."""
    x = np.concatenate([np.zeros(48000, dtype=np.float32), erzeuge_ltc_signal("10:00:00:00", 24, 3.0)])
    # davor Stille: Decoder sieht die Vorlauf-Frames nicht → tc_start bezieht sich auf Signalbeginn (Sekunde 0)
    e = decode_ltc(x, 48000)
    assert e.gueltig
    assert e.tc_start == "09:59:59:00"


def test_rauschen_liefert_keine_falschen_frames():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(48000 * 5).astype(np.float32) * 0.01
    e = decode_ltc(x, 48000)
    assert not e.gueltig


def test_bit_periode_grenzfall():
    """Regression: Referenz-Decoder lieferte NaN, wenn das 60. Perzentil exakt auf der Bitlänge lag."""
    x = erzeuge_ltc_signal("00:00:00:00", 24, 10.0)   # sehr viele 0-Bits → Perzentil-Grenzfall
    e = decode_ltc(x, 48000)
    assert e.gueltig and e.tc_start == "00:00:00:00"


def test_kanal_statistik_erkennt_ltc_und_stille():
    x = erzeuge_ltc_signal("01:02:03:04", 24, 2.0)
    s = kanal_statistik(x, 48000, 3, fps=24)
    assert s.ltc_kandidat and not s.stille
    st = kanal_statistik(np.zeros(96000, dtype=np.float32), 48000, 0)
    assert st.stille and not st.ltc_kandidat


@korpus
@pytest.mark.parametrize("datei,tc", sorted(ERWARTET.items()))
def test_korpus_t001_bis_t006(datei, tc):
    b = finde_ltc_in_video(str(VIDEO_DIR / datei), 4, fps=24, sekunden=10)
    assert b.kanal == 3, b.warnungen
    assert b.ergebnis.tc_start == tc
    assert b.ergebnis.fps == 24
    assert b.ergebnis.kontinuitaet >= 0.9
    # Kanäle 0–2 sind Stille
    assert all(s.stille for s in b.statistiken if s.kanal != 3)
