"""Synthetische Testdaten: BWF-WAV mit bext/iXML, LTC-Signal (Biphase-Mark-Encoder)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

_SYNC = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]


def _bext(time_reference: int, originator: str = "Test", date: str = "2023-11-17", time: str = "11:56:56") -> bytes:
    b = b"".ljust(256, b"\x00")                      # Description
    b += originator.encode().ljust(32, b"\x00")     # Originator
    b += b"REF".ljust(32, b"\x00")                  # OriginatorReference
    b += date.encode().ljust(10, b"\x00")
    b += time.encode().ljust(8, b"\x00")
    b += struct.pack("<II", time_reference & 0xFFFFFFFF, time_reference >> 32)
    b += struct.pack("<H", 1)                       # Version
    b += b"\x00" * 64                               # UMID
    b += b"\x00" * (2 * 5)                          # Loudness…
    b += b"\x00" * 180                              # Reserved
    b += b"A=PCM,F=48000,W=24,M=multi\r\n"
    return b


def _ixml(scene: str, take: str, tape: str, tracks: list[str], ts_lo: int | None, rate: str = "24/1") -> bytes:
    tr = "".join(f"<TRACK><CHANNEL_INDEX>{i+1}</CHANNEL_INDEX><INTERLEAVE_INDEX>{i+1}</INTERLEAVE_INDEX><NAME>{n}</NAME></TRACK>"
                 for i, n in enumerate(tracks))
    ts = f"<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI>0</TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI><TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>{ts_lo}</TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>" if ts_lo is not None else ""
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><BWFXML><IXML_VERSION>1.5</IXML_VERSION>'
           f'<SCENE>{scene}</SCENE><TAKE>{take}</TAKE><TAPE>{tape}</TAPE><CIRCLED>FALSE</CIRCLED><NOTE></NOTE>'
           f'<SPEED><TIMECODE_RATE>{rate}</TIMECODE_RATE><TIMECODE_FLAG>NDF</TIMECODE_FLAG>'
           f'<TIMESTAMP_SAMPLE_RATE>48000</TIMESTAMP_SAMPLE_RATE>{ts}</SPEED>'
           f'<TRACK_LIST><TRACK_COUNT>{len(tracks)}</TRACK_COUNT>{tr}</TRACK_LIST></BWFXML>')
    return xml.encode("utf-8")


def schreibe_bwf_wav(pfad: Path, *, sr: int = 48000, kanaele: int = 2, dauer_s: float = 2.0,
                     time_reference: int = 0, scene: str = "SZENE1-1", take: str = "001", tape: str = "231117",
                     tracks: list[str] | None = None, ixml_ts_lo: int | None = -1, bits: int = 16,
                     klick_bei_s: float | None = None) -> Path:
    """Minimal-BWF (RIFF/WAVE: bext, iXML, fmt, data). `ixml_ts_lo=-1` → identisch zu bext."""
    tracks = tracks or ["Record", "Safety"][:kanaele] + [f"Ch{i}" for i in range(2, kanaele)]
    n = int(sr * dauer_s)
    sig = np.zeros((n, kanaele), dtype=np.float64)
    if klick_bei_s is not None:
        i0 = int(klick_bei_s * sr)
        sig[i0:i0 + 48, :] = 0.9   # 1-ms-Klick
    if bits == 16:
        pcm = (sig * 32767).astype("<i2").tobytes()
    else:
        raise ValueError("nur 16 bit im Test-Helfer")
    fmt = struct.pack("<HHIIHH", 1, kanaele, sr, sr * kanaele * 2, kanaele * 2, 16)
    ts = time_reference if ixml_ts_lo == -1 else ixml_ts_lo
    chunks = [(b"bext", _bext(time_reference)), (b"iXML", _ixml(scene, take, tape, tracks, ts)),
              (b"fmt ", fmt), (b"data", pcm)]
    body = b""
    for cid, payload in chunks:
        body += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            body += b"\x00"
    with open(pfad, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    return pfad


def ltc_bits(hh: int, mm: int, ss: int, ff: int) -> list[int]:
    """80 Bit eines LTC-Frames (BCD little-endian je Feld, Sync-Wort 64–79)."""
    b = [0] * 80

    def put(lo: int, n: int, val: int) -> None:
        for j in range(n):
            b[lo + j] = (val >> j) & 1
    put(0, 4, ff % 10); put(8, 2, ff // 10)
    put(16, 4, ss % 10); put(24, 3, ss // 10)
    put(32, 4, mm % 10); put(40, 3, mm // 10)
    put(48, 4, hh % 10); put(56, 2, hh // 10)
    b[64:80] = _SYNC
    return b


def erzeuge_ltc_signal(start: str, fps: int, sekunden: float, sr: int = 48000, amplitude: float = 0.07) -> np.ndarray:
    """Biphase-Mark-Signal ab Timecode `start` ("HH:MM:SS:FF")."""
    hh, mm, ss, ff = (int(x) for x in start.split(":"))
    frame = ((hh * 60 + mm) * 60 + ss) * fps + ff
    n_frames = int(sekunden * fps) + 1
    T = sr / (80.0 * fps)
    level = 1.0
    out = []
    for k in range(n_frames):
        f = frame + k
        h = (f // (3600 * fps)) % 24
        m = (f // (60 * fps)) % 60
        s = (f // fps) % 60
        fr = f % fps
        for bit in ltc_bits(h, m, s, fr):
            level = -level                       # Übergang am Bitanfang
            half = int(round(T / 2))
            out.append(np.full(half, level, dtype=np.float32))
            if bit:
                level = -level                   # "1": zusätzlicher Übergang in Bitmitte
            out.append(np.full(int(round(T)) - half, level, dtype=np.float32))
    x = np.concatenate(out) * amplitude
    return x[: int(sekunden * sr)]
