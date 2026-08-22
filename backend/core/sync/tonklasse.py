"""Ton-Klassifikation vor der Transkription: Ist das überhaupt Nutzton?

Whisper halluziniert auf Stille, Timecode (LTC) und stationärem Rauschen („Thank you.“).
Vor `schritt_transkription` prüfen wir deshalb JEDEN Kanal der Quelle:

  stille      RMS < −60 dBFS oder < 2 % aktive Frames
  ltc         Biphase-Mark-Signal, dekodierbar (core.sync.ltc)
  rauschen    stationär + spektral flach (Brummen/Rauschen), kaum Dynamik
  nutzton     alles andere — kann Sprache/Atmo enthalten → darf zu Whisper

Nur `nutzton`-Kanäle werden zu Mono gemischt; gibt es keinen, wird die Transkription
übersprungen und der Grund im Job-Bericht genannt. Deterministisch, ohne ML.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .ltc import decode_ltc, kanal_lesen, kanal_statistik

KLASSE_STILLE = "stille"
KLASSE_LTC = "ltc"
KLASSE_RAUSCHEN = "rauschen"
KLASSE_NUTZTON = "nutzton"      # Sprache (VAD) oder — ohne VAD — unbestimmter Nutzton
KLASSE_ATMO = "atmo"            # Nutzton ohne erkennbare Sprache (VAD lief, fand nichts)

SPRACHE_MIN_S = 0.5             # ab so viel erkannter Sprache gilt ein Kanal als transkribierbar

_SR = 16000
_FRAME = 400          # 25 ms
_HOP = 160            # 10 ms


@dataclass
class KanalKlasse:
    kanal: int
    klasse: str
    rms_dbfs: float
    aktiv_anteil: float          # Anteil Frames > −45 dBFS
    flachheit: float             # mittlere spektrale Flachheit (0 = tonal, 1 = weißes Rauschen)
    dynamik_db: float            # P95 − P10 der Frame-Pegel
    detail: str
    sprache_s: Optional[float] = None    # erkannte Sprache in Sekunden (None = VAD nicht gelaufen)


# ─── VAD (Silero, lokal, optional) ─────────────────────────────────────────

_vad_modell = None
_vad_fehler: Optional[str] = None


def _vad():
    """Silero-VAD lazy laden; None, wenn Paket fehlt (dann bleibt es bei der Heuristik)."""
    global _vad_modell, _vad_fehler
    if _vad_modell is not None or _vad_fehler is not None:
        return _vad_modell
    try:
        from silero_vad import load_silero_vad
        _vad_modell = load_silero_vad(onnx=False)
    except Exception as e:  # noqa: BLE001
        _vad_fehler = str(e)
        _vad_modell = None
    return _vad_modell


def sprach_sekunden(x: np.ndarray, sr: int) -> Optional[float]:
    """Sekunden erkannter Sprache (Silero VAD, 16 kHz). None, wenn VAD nicht verfügbar."""
    m = _vad()
    if m is None:
        return None
    try:
        import torch
        from silero_vad import get_speech_timestamps
        y = x.astype(np.float32, copy=False)
        if sr != _SR:
            y = _resample_grob(y, sr)
        ts = get_speech_timestamps(torch.from_numpy(np.ascontiguousarray(y)), m, sampling_rate=_SR, return_seconds=True)
        return float(sum(t["end"] - t["start"] for t in ts))
    except Exception:  # noqa: BLE001
        return None


@dataclass
class TonBefund:
    kanaele: list[KanalKlasse]
    nutzton_kanaele: list[int]
    ltc_kanaele: list[int]
    zusammenfassung: str
    warnungen: list[str] = field(default_factory=list)

    @property
    def hat_nutzton(self) -> bool:
        """Mindestens ein Kanal mit Sprache (bzw. ohne VAD: mit Nutzsignal) → transkribierbar."""
        return bool(self.nutzton_kanaele)

    @property
    def ton_kanaele(self) -> list[int]:
        """Kanäle mit echtem Ton (Sprache ODER Atmo/Musik) — alles außer Stille/LTC/Rauschen."""
        return [c.kanal for c in self.kanaele if c.klasse in (KLASSE_NUTZTON, KLASSE_ATMO)]

    @property
    def hat_ton(self) -> bool:
        """False = die Spur ist als Ton unbrauchbar (nur Stille/Timecode/Rauschen) → nicht importieren."""
        return bool(self.ton_kanaele)


def _frame_pegel(x: np.ndarray) -> np.ndarray:
    n = (len(x) - _FRAME) // _HOP + 1
    if n <= 0:
        return np.array([-120.0])
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(n)[:, None]
    fr = x[idx]
    rms = np.sqrt(np.mean(fr * fr, axis=1)) + 1e-12
    return 20 * np.log10(rms)


def _spektrale_flachheit(x: np.ndarray) -> float:
    n = (len(x) - _FRAME) // _HOP + 1
    if n <= 0:
        return 1.0
    step = max(1, n // 200)                       # max ~200 Frames auswerten
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(0, n, step)[:, None]
    fr = x[idx] * np.hanning(_FRAME)[None, :]
    spec = np.abs(np.fft.rfft(fr, axis=1)) ** 2 + 1e-12
    # nur Frames mit Signal
    e = spec.sum(axis=1)
    spec = spec[e > np.percentile(e, 50)] if len(e) > 4 else spec
    geo = np.exp(np.mean(np.log(spec), axis=1))
    ari = np.mean(spec, axis=1)
    return float(np.mean(geo / ari))


def klassifiziere_signal(x: np.ndarray, sr: int, kanal: int = 0, fps: Optional[int] = None,
                         vad: bool = True, x16k: Optional[np.ndarray] = None) -> KanalKlasse:
    """Klassifiziert ein Mono-Signal (float32). `sr` beliebig; LTC-Prüfung erwartet ≥ 16 kHz.
    `vad=True`: Nutzton-Kanäle zusätzlich per Silero-VAD auf Sprache prüfen (→ nutzton | atmo)."""
    if len(x) < sr // 4:
        return KanalKlasse(kanal, KLASSE_STILLE, -120.0, 0.0, 1.0, 0.0, "zu kurz")
    x = x.astype(np.float32, copy=False)
    rms = float(np.sqrt(np.mean(x * x)))
    rms_db = 20 * np.log10(rms + 1e-12)
    pegel = _frame_pegel(x) if sr == _SR else _frame_pegel(_resample_grob(x, sr))
    aktiv = float(np.mean(pegel > -45.0))
    dyn = float(np.percentile(pegel, 95) - np.percentile(pegel, 10))
    if rms_db < -60.0 or aktiv < 0.02:
        return KanalKlasse(kanal, KLASSE_STILLE, rms_db, aktiv, 1.0, dyn, f"RMS {rms_db:.0f} dBFS, {aktiv:.0%} aktiv")
    # LTC?
    st = kanal_statistik(x, sr, kanal, fps)
    if st.ltc_kandidat:
        erg = decode_ltc(x, sr, fps)
        if erg.gueltig:
            return KanalKlasse(kanal, KLASSE_LTC, rms_db, aktiv, 0.0, dyn, f"LTC ab {erg.tc_start} ({erg.fps} fps)")
    flach = _spektrale_flachheit(x if sr == _SR else _resample_grob(x, sr))
    # Stationäres Rauschen/Brummen: praktisch keine Pegeldynamik (Sprache hat immer > 10 dB),
    # oder wenig Dynamik UND spektral flach / rechteckig.
    if dyn < 3.0 or (dyn < 6.0 and (flach > 0.35 or st.bimodalitaet > 0.6)):
        return KanalKlasse(kanal, KLASSE_RAUSCHEN, rms_db, aktiv, flach, dyn, f"stationär: Dynamik {dyn:.1f} dB, Flachheit {flach:.2f}")
    basis = f"Dynamik {dyn:.1f} dB, Flachheit {flach:.2f}, {aktiv:.0%} aktiv"
    if vad:
        # Für den VAD ein sauber resampeltes 16-kHz-Signal (x16k) verwenden — grobe Dezimierung
        # verfälscht die Sprach-Erkennung deutlich.
        sp = sprach_sekunden(x16k, _SR) if x16k is not None else sprach_sekunden(x, sr)
        if sp is not None:
            if sp >= SPRACHE_MIN_S:
                return KanalKlasse(kanal, KLASSE_NUTZTON, rms_db, aktiv, flach, dyn, f"Sprache {sp:.1f} s (VAD), {basis}", sp)
            return KanalKlasse(kanal, KLASSE_ATMO, rms_db, aktiv, flach, dyn, f"keine Sprache erkannt (VAD), {basis}", sp)
    return KanalKlasse(kanal, KLASSE_NUTZTON, rms_db, aktiv, flach, dyn, basis)


def _resample_grob(x: np.ndarray, sr: int) -> np.ndarray:
    """Grobe Dezimierung auf 16 kHz für die Pegel-/Spektralmaße (kein Anti-Alias nötig für Heuristik)."""
    f = max(1, int(round(sr / _SR)))
    return x[::f]


def klassifiziere_datei(pfad: str, kanaele: int, fps: Optional[int] = None, sekunden: float = 120.0,
                        ffmpeg_bin: str = "ffmpeg", vad: bool = True) -> TonBefund:
    """Alle Kanäle einer Mediendatei (Video oder Audio) klassifizieren — liest max. `sekunden` ab 0."""
    kk: list[KanalKlasse] = []
    for k in range(max(1, kanaele)):
        x = kanal_lesen(pfad, k, sekunden, 48000, ffmpeg_bin=ffmpeg_bin)
        vor = klassifiziere_signal(x, 48000, k, fps, vad=False)
        if vad and vor.klasse == KLASSE_NUTZTON:
            x16 = kanal_lesen(pfad, k, sekunden, _SR, ffmpeg_bin=ffmpeg_bin)   # echtes Resampling (ffmpeg)
            vor = klassifiziere_signal(x, 48000, k, fps, vad=True, x16k=x16)
        kk.append(vor)
    nutz = [c.kanal for c in kk if c.klasse == KLASSE_NUTZTON]
    ltc = [c.kanal for c in kk if c.klasse == KLASSE_LTC]
    teile = []
    for c in kk:
        teile.append(f"Kanal {c.kanal}: {c.klasse} ({c.detail})")
    if nutz:
        zus = f"Nutzton auf Kanal {', '.join(map(str, nutz))} — " + "; ".join(teile)
    else:
        zus = "Kein Nutzton — " + "; ".join(teile)
    warn: list[str] = []
    if not nutz:
        atmo = [c.kanal for c in kk if c.klasse == KLASSE_ATMO]
        if atmo:
            warn.append(f"Kein Sprachanteil erkannt (VAD) — Kanal {', '.join(map(str, atmo))} enthält Ton, aber keine Sprache; Transkription übersprungen")
        else:
            warn.append("Kamera-/Quellton enthält keinen Nutzton (nur Stille/Timecode/Rauschen) — Transkription übersprungen, sonst würde Whisper halluzinieren")
    if ltc:
        warn.append(f"Kanal {', '.join(map(str, ltc))} trägt LTC-Timecode und wird nicht in den Transkriptions-Mix genommen")
    return TonBefund(kk, nutz, ltc, zus, warn)
