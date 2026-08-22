"""Stufe 2 — Wellenform-Kreuzkorrelation (FFT, 8 kHz mono).

Ehrliche Grenzen: funktioniert nur, wenn die Kamera einen Scratch-Ton aufgezeichnet hat.
Auf dem Referenz-Korpus (SHORTCUT 24) sind die Kamera-Kanäle 0–2 stumm und Kanal 3 ist LTC
→ die Korrelation ist dort **nicht anwendbar** und meldet das explizit (`anwendbar=False`),
statt einen zufälligen Peak als Offset auszugeben.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

KORRELATIONS_SR = 8000
# Peak muss ≥ 3× über dem besten Peak außerhalb ±0,5 s liegen (Auftrag).
MIN_PEAK_VERHAELTNIS = 3.0
AUSSCHLUSS_S = 0.5


@dataclass
class KorrelationsErgebnis:
    anwendbar: bool
    offset_s: Optional[float]         # audio_start − video_start (signiert)
    peak_verhaeltnis: Optional[float]
    konfidenz: float
    begruendung: str
    warnungen: list[str] = field(default_factory=list)


def _lese_mono(pfad: str, kanal: int, sr: int, max_s: Optional[float],
               ffmpeg_bin: str = "ffmpeg") -> np.ndarray:
    cmd = [ffmpeg_bin, "-v", "error", "-nostdin"]
    if max_s:
        cmd += ["-t", f"{max_s:.3f}"]
    cmd += ["-i", pfad, "-vn", "-af", f"pan=mono|c0=c{kanal}", "-ar", str(sr), "-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(res.stdout, dtype=np.float32)


def _normiere(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float64, copy=False)
    x = x - x.mean()
    n = np.linalg.norm(x)
    return x / n if n > 0 else x


def kreuzkorrelation(a: np.ndarray, v: np.ndarray, sr: int) -> KorrelationsErgebnis:
    """Korreliert Audio (a) gegen Video-Scratch (v). Rückgabe-Offset = Start(a) − Start(v).

    Beide Signale sind mono float, gleiche Samplerate. Ein positiver Lag bedeutet: das
    Muster von `a` erscheint in `v` erst später → Audio startete *vor* dem Video →
    offset_s negativ (Konvention aus dem Auftrag: negativ = Ton lief zuerst).
    """
    if len(a) < sr or len(v) < sr:
        return KorrelationsErgebnis(False, None, None, 0.0, "Signal zu kurz für Korrelation")
    rms_a = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    rms_v = float(np.sqrt(np.mean(v.astype(np.float64) ** 2)))
    if rms_v < 1e-4:
        return KorrelationsErgebnis(False, None, None, 0.0,
                                    "Video-Kanal ist stumm — kein Scratch-Ton, Wellenform-Abgleich nicht anwendbar")
    if rms_a < 1e-4:
        return KorrelationsErgebnis(False, None, None, 0.0, "Audio-Kanal ist stumm")

    a_n = _normiere(a)
    v_n = _normiere(v)
    n = len(a_n) + len(v_n) - 1
    nfft = 1 << (n - 1).bit_length()
    A = np.fft.rfft(a_n, nfft)
    Vf = np.fft.rfft(v_n, nfft)
    cc = np.fft.irfft(A * np.conj(Vf), nfft)[:n]
    # Lags: Index i entspricht lag = i für i < len(a), sonst i - nfft (negativ).
    lags = np.arange(n)
    lags = np.where(lags < len(a_n), lags, lags - nfft)
    # cc[lag] hoch ⇔ a[t] ≈ v[t - lag]  → v ist um `lag` später ⇒ audio_start − video_start = -lag
    cc_abs = np.abs(cc)
    best = int(np.argmax(cc_abs))
    peak = float(cc_abs[best])
    lag = int(lags[best])
    aus = int(AUSSCHLUSS_S * sr)
    mask = np.abs(lags - lag) > aus
    zweit = float(cc_abs[mask].max()) if mask.any() else 0.0
    ratio = peak / zweit if zweit > 0 else float("inf")
    offset = -lag / sr
    if ratio < MIN_PEAK_VERHAELTNIS:
        return KorrelationsErgebnis(True, None, ratio, 0.0,
                                    f"Korrelationspeak nicht eindeutig (Verhältnis {ratio:.2f} < {MIN_PEAK_VERHAELTNIS})")
    konf = min(0.9, 0.5 + 0.1 * (ratio - MIN_PEAK_VERHAELTNIS))
    return KorrelationsErgebnis(True, offset, ratio, konf,
                                f"Wellenform: Peak-Verhältnis {ratio:.1f}, Offset {offset:+.3f} s")


def korreliere_dateien(audio_pfad: str, audio_kanal: int, video_pfad: str, video_kanal: int,
                       max_s: float = 120.0, ffmpeg_bin: str = "ffmpeg") -> KorrelationsErgebnis:
    a = _lese_mono(audio_pfad, audio_kanal, KORRELATIONS_SR, max_s, ffmpeg_bin)
    v = _lese_mono(video_pfad, video_kanal, KORRELATIONS_SR, max_s, ffmpeg_bin)
    return kreuzkorrelation(a, v, KORRELATIONS_SR)
