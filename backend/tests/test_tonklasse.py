import numpy as np

from backend.core.sync.tonklasse import (KLASSE_LTC, KLASSE_NUTZTON, KLASSE_RAUSCHEN, KLASSE_STILLE,
                                          klassifiziere_datei, klassifiziere_signal)
from backend.tests.conftest import AUDIO_DIR, VIDEO_DIR, korpus
from backend.tests.helfer import erzeuge_ltc_signal


def test_stille_und_ltc_und_rauschen():
    sr = 48000
    assert klassifiziere_signal(np.zeros(sr * 3, dtype=np.float32), sr).klasse == KLASSE_STILLE
    assert klassifiziere_signal(erzeuge_ltc_signal("10:00:00:00", 24, 3.0), sr, fps=24).klasse == KLASSE_LTC
    rng = np.random.default_rng(3)
    rausch = (rng.standard_normal(sr * 3) * 0.05).astype(np.float32)          # weißes, stationäres Rauschen
    assert klassifiziere_signal(rausch, sr).klasse == KLASSE_RAUSCHEN
    brumm = (0.05 * np.sin(2 * np.pi * 50 * np.arange(sr * 3) / sr)).astype(np.float32)   # 50-Hz-Brumm
    assert klassifiziere_signal(brumm, sr).klasse == KLASSE_RAUSCHEN


def test_sprachaehnliches_signal_ist_nutzton():
    """Modulierte, pausierte Töne (Silben-Rhythmus) → Dynamik + Struktur → nutzton."""
    sr = 16000
    t = np.arange(sr * 4) / sr
    huelle = (np.sin(2 * np.pi * 4 * t) > 0.2).astype(np.float32) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t))
    traeger = np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 360 * t) + 0.3 * np.sin(2 * np.pi * 900 * t)
    x = (0.2 * huelle * traeger).astype(np.float32)
    x[: sr] = 0.0                                                            # Sprechpause am Anfang
    assert klassifiziere_signal(x, sr, vad=False).klasse == KLASSE_NUTZTON       # Heuristik: Nutzton
    k = klassifiziere_signal(x, sr, vad=True)                                   # VAD: synthetische Töne ≠ Sprache
    assert k.klasse in ("atmo", KLASSE_NUTZTON)                                 # atmo, wenn Silero installiert ist
    if k.sprache_s is not None:
        assert k.klasse == "atmo"


@korpus
def test_korpus_kamera_ohne_nutzton_wav_mit_nutzton():
    b = klassifiziere_datei(str(VIDEO_DIR / "PPRM23_S004_S003_T001.MOV"), 4, fps=24)
    assert not b.hat_nutzton and b.ltc_kanaele == [3]
    assert [k.klasse for k in b.kanaele] == ["stille", "stille", "stille", "ltc"]
    w = klassifiziere_datei(str(AUDIO_DIR / "+SZENE4-3-002.WAV"), 6)
    assert 0 in w.nutzton_kanaele                                              # Spur „Record“
    rec = w.kanaele[0]
    if rec.sprache_s is not None:                                              # VAD vorhanden: Klappe + Rufe erkannt
        assert rec.sprache_s >= 1.0
