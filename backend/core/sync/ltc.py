"""LTC-Decoder (SMPTE 12M, Biphase-Mark) + Erkennung des LTC-Kanals in Video-Audiospuren.

Der Referenz-Decoder aus dem Auftrag wurde auf dem Korpus verifiziert (T001 → 12:57:04:07),
hatte aber zwei Schwächen, die hier behoben sind:

1. Bit-Periode `T = median(iv[iv > percentile(iv, 60)])` — liegt das 60. Perzentil exakt auf
   der Länge eines "0"-Bits, ist die Maske leer → NaN → 0 Frames (so auf T002/T003/T004/T006).
   Jetzt: robuste Zwei-Cluster-Schätzung (`>=`, hohes Perzentil, Nachschärfung).
2. Keine Plausibilitätsprüfung der BCD-Werte — auf Rausch-Kanälen liefert das Sync-Wort
   Zufallstreffer wie "41:33:71:36". Jetzt: Range-Check + Monotonie-/Kontinuitätsprüfung.

Deterministisch, keine Zufallszahlen, keine Netzwerk-/LLM-Aufrufe.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import numpy as np

_SYNC = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1], dtype=np.uint8)


@dataclass
class LtcFrame:
    bit_index: int
    hh: int
    mm: int
    ss: int
    ff: int
    drop_frame: bool
    sample_index: int = 0     # Sample-Position des ersten Bits im analysierten Ausschnitt

    @property
    def tc(self) -> str:
        sep = ";" if self.drop_frame else ":"
        return f"{self.hh:02d}:{self.mm:02d}:{self.ss:02d}{sep}{self.ff:02d}"

    def frame_number(self, fps: int) -> int:
        return ((self.hh * 60 + self.mm) * 60 + self.ss) * fps + self.ff


@dataclass
class LtcErgebnis:
    frames: list[LtcFrame]
    fps: Optional[int]                 # geschätzt aus max(ff)+1 bzw. Bitrate
    kontinuitaet: float                # Anteil aufeinanderfolgender +1-Frames (0..1)
    drop_frame: bool
    tc_start: Optional[str]            # TC am Anfang des analysierten Ausschnitts
    tc_start_seconds: Optional[float]  # Sekunden seit Mitternacht (Frame-genau)
    warnungen: list[str] = field(default_factory=list)

    @property
    def gueltig(self) -> bool:
        return self.tc_start is not None


# ─── Bit-Ebene ────────────────────────────────────────────────────────────

def _mittellinie(x: np.ndarray) -> float:
    """Schwelle zwischen den beiden Pegeln: Mitte der robusten Extreme (Perzentil 2/98).

    Der Median versagt bei einem exakt symmetrischen Rechteck (fällt auf einen Pegel →
    keine Nulldurchgänge); die Pegelmitte ist für Biphase-Signale die natürliche Schwelle.
    """
    lo, hi = np.percentile(x, [2, 98])
    return float((lo + hi) / 2.0)


def _bits_aus_signal(x: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """Biphase-Mark → Bitfolge. Rückgabe (bits, T, bit_pos): T = Bitdauer in Samples,
    bit_pos = Sample-Index des Anfangs jedes Bits (für die exakte Start-TC-Rückrechnung)."""
    x = x.astype(np.float32, copy=False)
    x = x - _mittellinie(x)
    s = np.sign(x)
    s[s == 0] = 1
    leer = np.zeros(0, dtype=np.uint8), float("nan"), np.zeros(0, dtype=np.int64)
    edges = np.where(np.diff(s) != 0)[0] + 1
    if len(edges) < 200:
        return leer
    iv = np.diff(edges).astype(np.float64)
    if len(iv) < 100:
        return leer
    # Robuste Periode: das obere Cluster (ganze Bits = "0") liegt bei ~T, das untere
    # (Halbbits = "1") bei ~T/2. Startschätzung über hohes Perzentil, dann Nachschärfen.
    t0 = float(np.percentile(iv, 85))
    upper = iv[iv >= 0.75 * t0]
    if len(upper) == 0:
        return leer
    T = float(np.median(upper))
    if not np.isfinite(T) or T <= 0:
        return leer

    bits: list[int] = []
    pos: list[int] = []
    i, n = 0, len(iv)
    while i < n:
        if iv[i] > 0.75 * T:
            bits.append(0); pos.append(int(edges[i]))
            i += 1
        elif i + 1 < n and iv[i] + iv[i + 1] < 1.35 * T:
            bits.append(1); pos.append(int(edges[i]))
            i += 2
        else:
            i += 1
    return np.asarray(bits, dtype=np.uint8), T, np.asarray(pos, dtype=np.int64)


def _bcd(f: np.ndarray, lo: int, n: int) -> int:
    v = 0
    for j in range(n):
        v |= int(f[lo + j]) << j
    return v


def dekodiere_bits(b: np.ndarray, fps_hint: Optional[int] = None,
                   bit_pos: Optional[np.ndarray] = None) -> list[LtcFrame]:
    """Sucht das 16-Bit-Sync-Wort und dekodiert 64-Bit-Frames mit Range-Check."""
    out: list[LtcFrame] = []
    n = len(b)
    if n < 80:
        return out
    # Vektorisierte Sync-Suche.
    win = np.lib.stride_tricks.sliding_window_view(b, 16)
    hits = np.where(np.all(win == _SYNC, axis=1))[0] - 64
    for k in hits:
        if k < 0 or k + 80 > n:
            continue
        f = b[k:k + 64]
        ff = _bcd(f, 0, 4) + 10 * _bcd(f, 8, 2)
        ss = _bcd(f, 16, 4) + 10 * _bcd(f, 24, 3)
        mm = _bcd(f, 32, 4) + 10 * _bcd(f, 40, 3)
        hh = _bcd(f, 48, 4) + 10 * _bcd(f, 56, 2)
        df = bool(f[10])
        if hh > 23 or mm > 59 or ss > 59 or ff > 59:
            continue
        if fps_hint and ff >= fps_hint:
            continue
        sample = int(bit_pos[k]) if bit_pos is not None and k < len(bit_pos) else 0
        out.append(LtcFrame(int(k), hh, mm, ss, ff, df, sample))
    return out


def decode_ltc(x: np.ndarray, sr: int, fps_hint: Optional[int] = None) -> LtcErgebnis:
    """Dekodiert einen Mono-Float-Ausschnitt eines Audiokanals.

    `sr` dient der fps-Schätzung über die Bitrate (80 Bit/Frame). `fps_hint`
    (z. B. aus der Videospur) verschärft die Plausibilitätsprüfung.
    """
    warn: list[str] = []
    bits, T, bit_pos = _bits_aus_signal(x)
    if len(bits) < 160:
        return LtcErgebnis([], None, 0.0, False, None, None, ["kein Biphase-Signal"])
    frames = dekodiere_bits(bits, fps_hint, bit_pos)
    if len(frames) < 3:
        return LtcErgebnis(frames, None, 0.0, False, None, None, ["< 3 gültige LTC-Frames"])

    # fps: aus Bitrate (sr / (80·T)) — robuster als max(ff)+1 auf kurzen Ausschnitten.
    fps_bitrate = sr / (80.0 * T) if np.isfinite(T) and T > 0 else float("nan")
    kandidaten = [24, 25, 30]
    fps = min(kandidaten, key=lambda c: abs(c - fps_bitrate)) if np.isfinite(fps_bitrate) else None
    if fps_hint and fps and fps != fps_hint:
        warn.append(f"LTC-Bitrate deutet auf {fps} fps, Video meldet {fps_hint} fps")
    if fps is None:
        fps = fps_hint or (max(fr.ff for fr in frames) + 1)
    drop = any(fr.drop_frame for fr in frames)
    if drop:
        warn.append("Drop-Frame-Flag gesetzt — DF-Arithmetik nicht implementiert, Werte als NDF interpretiert")

    # Kontinuität: aufeinanderfolgende Frames müssen +1 sein UND ~80 Bit auseinander liegen.
    ok = 0
    for a, b_ in zip(frames, frames[1:]):
        if b_.frame_number(fps) - a.frame_number(fps) == 1 and 70 <= (b_.bit_index - a.bit_index) <= 90:
            ok += 1
    kont = ok / (len(frames) - 1)

    if kont < 0.9:
        warn.append(f"LTC nicht kontinuierlich ({kont:.0%} monotone Frames)")
        return LtcErgebnis(frames, fps, kont, drop, None, None, warn)

    # Start-TC: erster gültiger Frame minus seine Sample-Position im Ausschnitt
    # (Sample-genau; unabhängig von führender Stille oder leicht schwankender Bitdauer).
    first = frames[0]
    start_seconds = first.frame_number(fps) / fps - first.sample_index / sr
    start_frame = start_seconds * fps
    total = int(round(start_frame))
    ff = total % fps
    tot_s = total // fps
    tc_start = f"{(tot_s // 3600) % 24:02d}:{(tot_s // 60) % 60:02d}:{tot_s % 60:02d}:{ff:02d}"
    return LtcErgebnis(frames, fps, kont, drop, tc_start, start_seconds, warn)


# ─── Kanal-Ebene ──────────────────────────────────────────────────────────

@dataclass
class KanalStatistik:
    kanal: int
    rms: float
    zcr: float             # Nulldurchgänge pro Sekunde
    bimodalitaet: float    # Anteil Samples nahe der Extremwerte (|x| > 0.7·max)
    stille: bool
    ltc_kandidat: bool


def kanal_lesen(video_pfad: str, kanal: int, sekunden: float = 10.0, sr: int = 48000,
                start_s: float = 0.0, ffmpeg_bin: str = "ffmpeg") -> np.ndarray:
    """Extrahiert einen Audiokanal als mono float32 (ohne Reencode der Datei)."""
    cmd = [ffmpeg_bin, "-v", "error", "-nostdin"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-t", f"{sekunden:.3f}", "-i", video_pfad, "-vn",
            "-af", f"pan=mono|c0=c{kanal}", "-ar", str(sr), "-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(res.stdout, dtype=np.float32)


def kanal_statistik(x: np.ndarray, sr: int, kanal: int, fps: Optional[int] = None) -> KanalStatistik:
    if len(x) < sr // 2:
        return KanalStatistik(kanal, 0.0, 0.0, 0.0, True, False)
    x = x.astype(np.float32, copy=False)
    rms = float(np.sqrt(np.mean(x * x)))
    stille = rms < 1e-4
    peak = float(np.max(np.abs(x))) or 1.0
    bimod = float(np.mean(np.abs(x) > 0.7 * peak))
    s = np.sign(x - _mittellinie(x)); s[s == 0] = 1
    zcr = float(np.count_nonzero(np.diff(s) != 0)) / (len(x) / sr)
    # LTC: 80 Bit/Frame, "0" = 1 Flanke, "1" = 2 Flanken → ZCR zwischen 80·fps und 160·fps.
    kandidat = False
    if not stille and bimod > 0.6:
        for f in ([fps] if fps else [24, 25, 30]):
            if 80 * f * 1.0 <= zcr <= 80 * f * 2.05:
                kandidat = True
    return KanalStatistik(kanal, rms, zcr, bimod, stille, kandidat)


@dataclass
class VideoLtcBefund:
    kanal: Optional[int]
    ergebnis: Optional[LtcErgebnis]
    statistiken: list[KanalStatistik]
    warnungen: list[str] = field(default_factory=list)


def finde_ltc_in_video(video_pfad: str, kanaele: int, fps: Optional[int] = None,
                       sekunden: float = 10.0, sr: int = 48000,
                       ffmpeg_bin: str = "ffmpeg") -> VideoLtcBefund:
    """Prüft alle Audiokanäle einer Videodatei auf LTC. Erster gültiger Kanal gewinnt.

    Reihenfolge: statistische Kandidaten zuerst; danach — falls keiner dekodierbar —
    ein Voll-Versuch auf allen nicht-stillen Kanälen (Statistik ist nur Heuristik).
    """
    stats: list[KanalStatistik] = []
    signale: dict[int, np.ndarray] = {}
    for k in range(kanaele):
        x = kanal_lesen(video_pfad, k, sekunden, sr, ffmpeg_bin=ffmpeg_bin)
        signale[k] = x
        stats.append(kanal_statistik(x, sr, k, fps))

    reihenfolge = [s.kanal for s in stats if s.ltc_kandidat] + \
                  [s.kanal for s in stats if not s.ltc_kandidat and not s.stille]
    warn: list[str] = []
    for k in reihenfolge:
        erg = decode_ltc(signale[k], sr, fps)
        if erg.gueltig:
            if not stats[k].ltc_kandidat:
                warn.append(f"LTC auf Kanal {k} dekodiert, obwohl Statistik ihn nicht als Kandidat führte")
            return VideoLtcBefund(k, erg, stats, warn + erg.warnungen)
    return VideoLtcBefund(None, None, stats, warn + ["kein dekodierbarer LTC-Kanal"])
