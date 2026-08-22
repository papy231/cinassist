"""BWF (`bext`) + iXML-Parser für WAV-Dateien — ohne externe Abhängigkeit.

ffprobe zeigt `bext` als Tags (time_reference …), aber nur einen Teil des iXML
(als `comment`-String). Wir lesen die RIFF-Chunks direkt: das ist ~60 Zeilen,
deterministisch, und liefert TRACK_LIST / TIMECODE_RATE / TIMESTAMP_* sauber.

Nur die Chunks werden gelesen (kein `data`-Inhalt) → auch bei 70-MB-Dateien
im Millisekundenbereich.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

# Größe des Fixteils von `bext` bis einschl. `Reserved` (EBU Tech 3285 v2).
_BEXT_FIXED = 256 + 32 + 32 + 10 + 8 + 4 + 4 + 2 + 64 + 2 + 2 + 2 + 2 + 2 + 180


@dataclass
class BextChunk:
    description: str = ""
    originator: str = ""
    originator_reference: str = ""
    origination_date: str = ""     # "YYYY-MM-DD"
    origination_time: str = ""     # "HH:MM:SS"
    time_reference: int = 0        # Samples seit Mitternacht
    version: int = 0
    coding_history: str = ""


@dataclass
class IxmlTrack:
    channel_index: int
    interleave_index: int
    name: str


@dataclass
class IxmlChunk:
    raw: str = ""
    project: Optional[str] = None
    scene: Optional[str] = None
    take: Optional[str] = None
    tape: Optional[str] = None
    circled: Optional[bool] = None
    note: Optional[str] = None
    timecode_rate: Optional[str] = None       # z. B. "24/1"
    timecode_flag: Optional[str] = None       # "NDF" / "DF"
    timestamp_sample_rate: Optional[int] = None
    timestamp_samples_since_midnight: Optional[int] = None
    original_filename: Optional[str] = None
    current_filename: Optional[str] = None
    tracks: list[IxmlTrack] = field(default_factory=list)

    def track_index_by_name(self, name: str) -> Optional[int]:
        """0-basierter Kanalindex einer Spur nach Name (case-/whitespace-tolerant)."""
        wanted = name.strip().lower()
        for t in self.tracks:
            if t.name.strip().lower() == wanted:
                return t.interleave_index - 1
        return None


@dataclass
class WavInfo:
    pfad: str
    sample_rate: int
    kanaele: int
    bits: int
    data_bytes: int
    dauer_s: float
    bext: Optional[BextChunk]
    ixml: Optional[IxmlChunk]
    warnungen: list[str] = field(default_factory=list)

    # ─── Timecode-Ableitung ────────────────────────────────────────────────
    @property
    def tc_rate(self) -> Optional[Fraction]:
        if self.ixml and self.ixml.timecode_rate:
            try:
                return Fraction(self.ixml.timecode_rate)
            except (ValueError, ZeroDivisionError):
                return None
        return None

    @property
    def tc_start_seconds(self) -> Optional[float]:
        """Startzeit als Sekunden seit Mitternacht (aus bext.time_reference).

        Cross-Check gegen iXML.TIMESTAMP_SAMPLES_SINCE_MIDNIGHT: bei Abweichung
        → None + Warnung (Spec: `tc_quelle = keine`).
        """
        if not self.bext:
            return None
        sr = self.sample_rate
        if self.ixml and self.ixml.timestamp_sample_rate:
            # Timestamp bezieht sich laut iXML u. U. auf eine andere Rate.
            sr_ts = self.ixml.timestamp_sample_rate
        else:
            sr_ts = sr
        bext_s = self.bext.time_reference / sr
        if self.ixml and self.ixml.timestamp_samples_since_midnight is not None:
            ixml_s = self.ixml.timestamp_samples_since_midnight / sr_ts
            if abs(ixml_s - bext_s) > 0.001:
                self.warnungen.append(
                    f"bext.time_reference ({bext_s:.3f}s) ≠ iXML.TIMESTAMP ({ixml_s:.3f}s)"
                )
                return None
        return bext_s

    @property
    def tc_quelle(self) -> str:
        if self.tc_start_seconds is None:
            return "keine"
        return "bwf"

    def tc_start_str(self) -> Optional[str]:
        s = self.tc_start_seconds
        if s is None:
            return None
        rate = self.tc_rate or Fraction(24)
        return sekunden_zu_tc(s, rate)


# ─── Hilfsfunktionen ──────────────────────────────────────────────────────

def sekunden_zu_tc(sekunden: float, rate: Fraction) -> str:
    """Sekunden seit Mitternacht → "HH:MM:SS:FF" (NDF, Frames abgerundet)."""
    total_frames = int(round(sekunden * float(rate)))
    fps = int(round(float(rate)))
    ff = total_frames % fps
    total_s = total_frames // fps
    ss = total_s % 60
    mm = (total_s // 60) % 60
    hh = (total_s // 3600) % 24
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def tc_zu_sekunden(tc: str, rate: Fraction) -> float:
    """"HH:MM:SS:FF" (oder "HH:MM:SS;FF") → Sekunden seit Mitternacht."""
    tc = tc.replace(";", ":")
    hh, mm, ss, ff = (int(p) for p in tc.split(":"))
    return hh * 3600 + mm * 60 + ss + ff / float(rate)


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()


def _parse_bext(payload: bytes) -> BextChunk:
    if len(payload) < _BEXT_FIXED:
        payload = payload.ljust(_BEXT_FIXED, b"\x00")
    off = 0
    description = _cstr(payload[off:off + 256]); off += 256
    originator = _cstr(payload[off:off + 32]); off += 32
    originator_ref = _cstr(payload[off:off + 32]); off += 32
    orig_date = _cstr(payload[off:off + 10]); off += 10
    orig_time = _cstr(payload[off:off + 8]); off += 8
    tr_low, tr_high = struct.unpack_from("<II", payload, off); off += 8
    version, = struct.unpack_from("<H", payload, off); off += 2
    coding = payload[_BEXT_FIXED:].decode("latin-1", "replace").strip("\x00 \r\n")
    return BextChunk(
        description=description,
        originator=originator,
        originator_reference=originator_ref,
        origination_date=orig_date,
        origination_time=orig_time,
        time_reference=(tr_high << 32) | tr_low,
        version=version,
        coding_history=coding,
    )


def _text(root: ET.Element, path: str) -> Optional[str]:
    el = root.find(path)
    if el is None or el.text is None:
        return None
    return el.text.strip()


def _parse_ixml(payload: bytes) -> IxmlChunk:
    raw = payload.decode("utf-8", "replace").strip("\x00 \r\n\t")
    ix = IxmlChunk(raw=raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ix
    ix.project = _text(root, "PROJECT")
    ix.scene = _text(root, "SCENE")
    ix.take = _text(root, "TAKE")
    ix.tape = _text(root, "TAPE")
    circled = _text(root, "CIRCLED")
    if circled is not None:
        ix.circled = circled.upper() == "TRUE"
    ix.note = _text(root, "NOTE")
    ix.timecode_rate = _text(root, "SPEED/TIMECODE_RATE")
    ix.timecode_flag = _text(root, "SPEED/TIMECODE_FLAG")
    tsr = _text(root, "SPEED/TIMESTAMP_SAMPLE_RATE")
    if tsr and tsr.isdigit():
        ix.timestamp_sample_rate = int(tsr)
    lo = _text(root, "SPEED/TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO")
    hi = _text(root, "SPEED/TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI")
    if lo and lo.isdigit():
        ix.timestamp_samples_since_midnight = int(lo) + ((int(hi) << 32) if hi and hi.isdigit() else 0)
    ix.original_filename = _text(root, "HISTORY/ORIGINAL_FILENAME")
    ix.current_filename = _text(root, "HISTORY/CURRENT_FILENAME")
    for tr in root.findall("TRACK_LIST/TRACK"):
        ci = _text(tr, "CHANNEL_INDEX")
        ii = _text(tr, "INTERLEAVE_INDEX")
        name = _text(tr, "NAME") or ""
        try:
            ix.tracks.append(IxmlTrack(int(ci or 0), int(ii or ci or 0), name))
        except ValueError:
            continue
    return ix


def lese_wav(pfad: str | Path) -> WavInfo:
    """Liest fmt/bext/iXML/data-Größe einer WAV-/RF64-Datei (nur Chunk-Header)."""
    pfad = str(pfad)
    sample_rate = kanaele = bits = 0
    data_bytes = 0
    bext = ixml = None
    warn: list[str] = []
    ds64_data_size: Optional[int] = None

    with open(pfad, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[0:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
            raise ValueError(f"Keine WAV-Datei: {pfad}")
        is_rf64 = head[0:4] == b"RF64"
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = struct.unpack("<4sI", hdr)
            if is_rf64 and cid == b"ds64":
                payload = f.read(size)
                # riffSize(8) dataSize(8) sampleCount(8) …
                if len(payload) >= 16:
                    ds64_data_size, = struct.unpack_from("<Q", payload, 8)
            elif cid == b"fmt ":
                payload = f.read(size)
                if len(payload) >= 16:
                    _fmt, kanaele, sample_rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", payload, 0)
            elif cid == b"bext":
                bext = _parse_bext(f.read(size))
            elif cid == b"iXML":
                ixml = _parse_ixml(f.read(size))
            elif cid == b"data":
                data_bytes = ds64_data_size if (is_rf64 and size == 0xFFFFFFFF and ds64_data_size) else size
                # Inhalt überspringen (kann > Dateiende sein bei defekten Files).
                f.seek(data_bytes, 1)
            else:
                f.seek(size, 1)
            if size & 1:
                f.seek(1, 1)  # RIFF-Padding

    if not sample_rate or not kanaele or not bits:
        raise ValueError(f"WAV ohne gültigen fmt-Chunk: {pfad}")
    dauer = data_bytes / (sample_rate * kanaele * (bits // 8)) if data_bytes else 0.0
    return WavInfo(
        pfad=pfad, sample_rate=sample_rate, kanaele=kanaele, bits=bits,
        data_bytes=data_bytes, dauer_s=dauer, bext=bext, ixml=ixml, warnungen=warn,
    )
