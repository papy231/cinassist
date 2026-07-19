"""
CinAssist — Multi-Kamera Audio-Sync via Cross-Correlation (Vague 5.1).

Findet den zeitlichen Offset zwischen mehreren gleichzeitig aufgenommenen
Videoclips über die Audio-Kreuzkorrelation (FFT-basiert).

Standard-Workflow im Doku-/Interview-Kontext:
    - Kamera A (Master) und Kamera B (Zweiter Winkel) starten leicht versetzt
    - Beide Kameras nehmen das gleiche Audio auf (unterschiedliche Position/
      Qualität), aber der Zeitverlauf ist identisch
    - Cross-Correlation der beiden Audio-Envelopes findet den Peak-Offset

Ergebnis: Offset in Sekunden für jeden Clip relativ zum Master.
"""
from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger("cinassist.multicam_sync")


def _extract_audio_envelope(video_path: str, temp_dir: Path, sr: int = 8000, max_duration_s: float = 60.0) -> tuple[list[float], int]:
    """Extrahiert die Audio-Envelope (RMS pro Fenster) für Cross-Correlation.

    Downsampling auf 8kHz + Envelope statt Rohaudio → 10× schnellere FFT
    ohne Verlust an Sync-Präzision (Millisekunden-Genauigkeit reicht).
    """
    try:
        import numpy as np
        import librosa
    except ImportError as e:
        raise RuntimeError(f"librosa/numpy nicht installiert: {e}")

    tmp_audio = temp_dir / f"sync_{uuid.uuid4().hex[:8]}.wav"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vn", "-ac", "1", "-ar", str(sr),
             "-t", str(max_duration_s),
             "-f", "wav", str(tmp_audio)],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0 or not tmp_audio.exists() or tmp_audio.stat().st_size < 1000:
            raise RuntimeError(f"Audio-Extraktion fehlgeschlagen: {proc.stderr.decode('utf-8', 'replace')[-200:]}")

        y, sr_out = librosa.load(str(tmp_audio), sr=sr, mono=True)
        if len(y) == 0:
            raise RuntimeError("Extrahiertes Audio ist leer")

        # Envelope = RMS mit Fenster ~10ms (80 samples bei 8000 Hz)
        frame_length = int(sr * 0.02)
        hop_length = int(sr * 0.01)
        rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
        envelope_sr = int(sr / hop_length)
        return rms.tolist(), envelope_sr
    finally:
        tmp_audio.unlink(missing_ok=True)


def _find_offset(env_a: list[float], env_b: list[float], envelope_sr: int) -> tuple[float, float]:
    """Findet den Zeitversatz (Sekunden) zwischen zwei Envelopes via FFT-Cross-Correlation.

    Returns (offset_seconds, confidence 0..1).
    Positiver offset = B ist relativ zu A verspätet.
    """
    import numpy as np
    from scipy import signal as spsignal

    a = np.array(env_a, dtype=np.float32)
    b = np.array(env_b, dtype=np.float32)
    # Normalisierung
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)

    corr = spsignal.correlate(a, b, mode="full", method="fft")
    lags = spsignal.correlation_lags(len(a), len(b), mode="full")

    peak_idx = int(np.argmax(corr))
    lag = int(lags[peak_idx])
    offset_s = float(lag / envelope_sr)

    # Confidence: Peak-Höhe relativ zum Mittelwert der Nachbarschaft
    peak_val = float(corr[peak_idx])
    neighborhood = corr[max(0, peak_idx - 100): peak_idx + 100]
    baseline = float(np.median(np.abs(neighborhood)))
    confidence = min(1.0, peak_val / (baseline * 3 + 1e-9)) if baseline > 0 else 0.0

    return offset_s, round(confidence, 3)


def sync_clips(clip_paths: list[tuple[str, str]], temp_dir: Path) -> dict:
    """
    Berechnet Zeitversätze zwischen mehreren Clips (paarweise gegen den ersten
    Clip als Referenz-Master).

    Args:
        clip_paths: Liste von Tupeln (clip_id, video_path)
        temp_dir: Ordner für temporäre WAV-Dateien

    Returns:
        {
          "master_clip_id": "...",
          "offsets": [
             {"clip_id": "...", "offset_s": 0.0, "confidence": 1.0},   # Master
             {"clip_id": "...", "offset_s": 2.34, "confidence": 0.87},
             ...
          ],
          "average_confidence": 0.85,
          "envelope_sr": 100,
        }
    """
    if len(clip_paths) < 2:
        return {"error": "sync benötigt mindestens 2 Clips"}

    envelopes: dict[str, list[float]] = {}
    envelope_sr = 0
    for clip_id, video_path in clip_paths:
        try:
            env, sr = _extract_audio_envelope(video_path, temp_dir)
            envelopes[clip_id] = env
            envelope_sr = sr
        except Exception as e:
            logger.warning("Envelope-Extraktion fehlgeschlagen für %s: %s", clip_id, e)

    if len(envelopes) < 2:
        return {"error": "zu wenige verwertbare Audiospuren (< 2)"}

    # Erster Clip = Master (Offset 0)
    master_id = clip_paths[0][0]
    if master_id not in envelopes:
        # Falls Master-Envelope fehlt, den ersten verwertbaren Clip als Master nehmen
        master_id = next(iter(envelopes.keys()))
    master_env = envelopes[master_id]

    offsets = [{"clip_id": master_id, "offset_s": 0.0, "confidence": 1.0}]
    confidences: list[float] = []
    for cid, env in envelopes.items():
        if cid == master_id:
            continue
        try:
            off, conf = _find_offset(master_env, env, envelope_sr)
            offsets.append({"clip_id": cid, "offset_s": round(off, 3), "confidence": conf})
            confidences.append(conf)
        except Exception as e:
            logger.warning("Sync-Fehler zwischen %s und %s: %s", master_id, cid, e)
            offsets.append({"clip_id": cid, "offset_s": 0.0, "confidence": 0.0, "error": str(e)})

    avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    return {
        "master_clip_id": master_id,
        "offsets": offsets,
        "average_confidence": avg_conf,
        "envelope_sr": envelope_sr,
    }
