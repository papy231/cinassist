"""Ton-Ausrichtung im Ingest: `_ton_ausrichten` schneidet/verzögert das verknüpfte WAV um offset_s
so, dass Sekunde 0 des Ergebnisses = Sekunde 0 des Videos ist."""

import subprocess

import numpy as np

from backend.tests.helfer import schreibe_bwf_wav


def _klick_position(pfad: str) -> float:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", pfad, "-f", "f32le", "-ac", "1", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    return int(np.argmax(np.abs(x) > 0.5)) / 48000.0


def test_ton_ausrichten_negativer_und_positiver_offset(tmp_path, monkeypatch):
    from backend.workers import ingest
    monkeypatch.setattr(ingest, "TEMP_DIR", tmp_path)
    wav = schreibe_bwf_wav(tmp_path / "SZENE1-1-001.WAV", kanaele=2, dauer_s=8.0, klick_bei_s=5.0)

    # Ton lief 2,7 s vor dem Bild los → Klick liegt im Video bei 5,0 − 2,7 = 2,3 s
    p = ingest._ton_ausrichten({"audio_pfad": str(wav), "kanal": 0, "offset_s": -2.7}, video_dauer=4.0, job_id="j")
    assert p is not None
    assert abs(_klick_position(p) - 2.3) < 0.01
    # -t video_dauer respektiert
    dauer = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                 capture_output=True, text=True).stdout.strip())
    assert abs(dauer - 4.0) < 0.05

    # Ton startete 1,5 s NACH dem Bild → Stille davor, Klick bei 6,5 s
    p2 = ingest._ton_ausrichten({"audio_pfad": str(wav), "kanal": 1, "offset_s": 1.5}, video_dauer=None, job_id="j")
    assert p2 is not None
    assert abs(_klick_position(p2) - 6.5) < 0.01


def test_ton_ausrichten_fehlende_datei(tmp_path, monkeypatch):
    from backend.workers import ingest
    monkeypatch.setattr(ingest, "TEMP_DIR", tmp_path)
    assert ingest._ton_ausrichten({"audio_pfad": str(tmp_path / "gibts_nicht.wav"), "kanal": 0, "offset_s": 0.0}, 10.0, "j") is None
    assert ingest._ton_ausrichten(None, 10.0, "j") is None
