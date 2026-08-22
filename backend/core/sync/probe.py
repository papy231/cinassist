"""Ordner-Scan + Datei-Analyse (ffprobe / BWF / LTC) → `AssetProbe` (DB-frei).

Regeln aus dem Auftrag:
- Originale werden NIE kopiert oder reencodiert — nur gelesen (Chunk-Header, 10 s Audio).
- ExFAT-Resource-Forks `._<name>` tragen die Video-Endung → am Scan filtern.
- Container-`timecode`-Tag wird verworfen, wenn er auf ≥ 3 Dateien desselben Imports identisch ist
  (Export-Artefakt, so auf dem Referenz-Korpus: 16:46:20:04 überall).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Optional

from .bwf_ixml import lese_wav, sekunden_zu_tc, tc_zu_sekunden
from .ltc import finde_ltc_in_video, kanal_lesen, kanal_statistik
from .namen import (NamensTeile, parse_audio_name, parse_datum_aus_dateiname,
                    parse_ordner_datum, parse_tape_datum, parse_video_name)

VIDEO_ENDUNGEN = {".mov", ".mp4", ".mxf", ".avi", ".mkv", ".webm", ".m4v", ".mts", ".m2ts"}
AUDIO_ENDUNGEN = {".wav", ".bwf", ".rf64", ".aif", ".aiff", ".flac", ".caf"}
IGNORIERTE_ORDNER = {"$RECYCLE.BIN", "TRASH", "UNDO", "SETTINGS", ".Spotlight-V100", ".Trashes",
                     ".fseventsd", "System Volume Information", ".TemporaryItems", "__MACOSX"}
FINGERPRINT_BYTES = 4 * 1024 * 1024
CONTAINER_TC_MAX_GLEICH = 3   # ab so vielen identischen Container-TCs pro Import: verwerfen


# ─── Scan ─────────────────────────────────────────────────────────────────

@dataclass
class ScanErgebnis:
    pfad: str
    typ: str
    dateien: list[str]
    ignoriert: int
    ignoriert_beispiele: list[str] = field(default_factory=list)


def ist_ignorierte_datei(name: str) -> bool:
    return name.startswith("._") or name.startswith(".") or name in ("Thumbs.db", "desktop.ini")


def scanne_ordner(pfad: str | Path, typ: str) -> ScanErgebnis:
    """Rekursiver Scan; `typ` ∈ {video, audio}. Deterministisch sortiert."""
    root = Path(pfad)
    if not root.is_dir():
        raise FileNotFoundError(f"Ordner nicht gefunden: {root}")
    endungen = VIDEO_ENDUNGEN if typ == "video" else AUDIO_ENDUNGEN
    dateien: list[str] = []
    ignoriert = 0
    beispiele: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Ordner-Filter in-place (os.walk respektiert das).
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORIERTE_ORDNER and not d.startswith("."))
        for fn in sorted(filenames):
            ext = Path(fn).suffix.lower()
            if ext not in endungen:
                continue
            if ist_ignorierte_datei(fn):
                ignoriert += 1
                if len(beispiele) < 5:
                    beispiele.append(fn)
                continue
            dateien.append(str(Path(dirpath) / fn))
    return ScanErgebnis(str(root), typ, dateien, ignoriert, beispiele)


# ─── Fingerprint / Volume ─────────────────────────────────────────────────

def fingerprint(pfad: str | Path) -> str:
    """sha256(erste 4 MB) + Dateigröße — Idempotenz ohne Voll-Hash großer ProRes-Dateien."""
    p = Path(pfad)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(FINGERPRINT_BYTES))
    h.update(str(p.stat().st_size).encode())
    return h.hexdigest()


def volume_root(pfad: str | Path) -> Optional[str]:
    p = Path(pfad).resolve()
    parts = p.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return str(Path("/", "Volumes", parts[2]))
    return None


def volume_uuid(pfad: str | Path) -> Optional[str]:
    """macOS: `diskutil info <mount>` → Volume UUID. Sonst None (best-effort)."""
    root = volume_root(pfad)
    if not root:
        return None
    try:
        out = subprocess.run(["diskutil", "info", root], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Volume UUID:\s*([0-9A-Fa-f-]+)", out)
    return m.group(1) if m else None


def volume_gemountet(pfad: str | Path) -> bool:
    root = volume_root(pfad)
    return True if root is None else Path(root).exists()


# ─── ffprobe ──────────────────────────────────────────────────────────────

def ffprobe_json(pfad: str, ffprobe_bin: str = "ffprobe") -> dict:
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", pfad]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe fehlgeschlagen: {res.stderr.strip()[:200]}")
    return json.loads(res.stdout or "{}")


def _fps_aus_stream(s: dict) -> Optional[float]:
    for key in ("r_frame_rate", "avg_frame_rate"):
        v = s.get(key)
        if v and v != "0/0":
            try:
                return float(Fraction(v))
            except (ValueError, ZeroDivisionError):
                continue
    return None


# ─── Ergebnis-Struktur ────────────────────────────────────────────────────

@dataclass
class AssetProbe:
    typ: str
    pfad: str
    dateiname: str
    dauer_s: float
    sample_rate: Optional[int]
    kanaele: Optional[int]
    fps: Optional[float]
    codec: Optional[str]
    dateigroesse: int
    tc_start: Optional[str]              # "HH:MM:SS:FF"
    tc_start_s: Optional[float]          # Sekunden seit Mitternacht
    tc_quelle: str                       # bwf | ixml | ltc | container | keine
    tc_rate: Optional[str]               # "24/1"
    tc_flag: Optional[str]               # NDF | DF
    ixml_json: Optional[dict]
    fingerprint: str
    namen: NamensTeile
    datum: Optional[date]
    ltc_kanal: Optional[int] = None
    scratch_kanal: Optional[int] = None
    record_kanal: int = 0
    container_tc: Optional[str] = None
    unbekannte_markierung: Optional[str] = None
    warnungen: list[str] = field(default_factory=list)

    def als_dict(self) -> dict:
        d = asdict(self)
        d["namen"] = asdict(self.namen)
        d["datum"] = self.datum.isoformat() if self.datum else None
        return d


# ─── Audio ────────────────────────────────────────────────────────────────

def analysiere_audio(pfad: str, ordnername: Optional[str] = None, ffprobe_bin: str = "ffprobe") -> AssetProbe:
    p = Path(pfad)
    groesse = p.stat().st_size
    warn: list[str] = []
    ext = p.suffix.lower()
    if ext in (".wav", ".bwf", ".rf64"):
        w = lese_wav(p)
        tc_s = w.tc_start_seconds
        warn += w.warnungen
        ix = w.ixml
        namen = parse_audio_name(p.name, ix.scene if ix else None, ix.take if ix else None)
        rate = w.tc_rate
        tc_str = sekunden_zu_tc(tc_s, rate or Fraction(24)) if tc_s is not None else None
        rec = 0
        if ix:
            r = ix.track_index_by_name("Record")
            if r is not None and 0 <= r < w.kanaele:
                rec = r
            else:
                warn.append("Keine Spur „Record“ in iXML TRACK_LIST — Kanal 0 für Transkription")
        else:
            warn.append("Kein iXML-Chunk — Kanal 0 für Transkription")
        if not w.bext:
            warn.append("Kein bext-Chunk — kein Timecode")
        datum = None
        if ix and ix.tape:
            datum = parse_tape_datum(ix.tape)
        datum = datum or parse_ordner_datum(ordnername) or parse_datum_aus_dateiname(p.name) \
            or (date.fromisoformat(w.bext.origination_date) if w.bext and re.fullmatch(r"\d{4}-\d{2}-\d{2}", w.bext.origination_date or "") else None)
        if ix and ix.original_filename and ix.current_filename and ix.original_filename != ix.current_filename:
            warn.append(f"Am Gerät umbenannt: {ix.original_filename} → {ix.current_filename}")
        ixml_json = None
        if ix:
            ixml_json = {
                "scene": ix.scene, "take": ix.take, "tape": ix.tape, "circled": ix.circled, "note": ix.note,
                "project": ix.project, "timecode_rate": ix.timecode_rate, "timecode_flag": ix.timecode_flag,
                "timestamp_samples_since_midnight": ix.timestamp_samples_since_midnight,
                "original_filename": ix.original_filename, "current_filename": ix.current_filename,
                "tracks": [{"index": t.interleave_index, "name": t.name} for t in ix.tracks],
                "bext": {"time_reference": w.bext.time_reference, "originator": w.bext.originator,
                         "origination_date": w.bext.origination_date, "origination_time": w.bext.origination_time,
                         "originator_reference": w.bext.originator_reference} if w.bext else None,
                "raw": ix.raw,
            }
        return AssetProbe(
            typ="audio", pfad=str(p), dateiname=p.name, dauer_s=round(w.dauer_s, 6),
            sample_rate=w.sample_rate, kanaele=w.kanaele, fps=None,
            codec=f"pcm_s{w.bits}le", dateigroesse=groesse,
            tc_start=tc_str, tc_start_s=tc_s, tc_quelle=w.tc_quelle,
            tc_rate=str(rate) if rate else None, tc_flag=ix.timecode_flag if ix else None,
            ixml_json=ixml_json, fingerprint=fingerprint(p), namen=namen, datum=datum,
            record_kanal=rec, unbekannte_markierung=namen.unbekannte_markierung, warnungen=warn,
        )
    # Nicht-WAV-Audio: nur ffprobe, kein Timecode.
    info = ffprobe_json(str(p), ffprobe_bin)
    st = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = info.get("format", {})
    warn.append(f"{ext}: kein BWF/iXML — kein Timecode")
    return AssetProbe(
        typ="audio", pfad=str(p), dateiname=p.name, dauer_s=float(fmt.get("duration") or 0),
        sample_rate=int(st.get("sample_rate") or 0) or None, kanaele=st.get("channels"), fps=None,
        codec=st.get("codec_name"), dateigroesse=groesse, tc_start=None, tc_start_s=None,
        tc_quelle="keine", tc_rate=None, tc_flag=None, ixml_json=None, fingerprint=fingerprint(p),
        namen=parse_audio_name(p.name), datum=parse_ordner_datum(ordnername) or parse_datum_aus_dateiname(p.name),
        warnungen=warn,
    )


# ─── Video ────────────────────────────────────────────────────────────────

def analysiere_video(pfad: str, ffprobe_bin: str = "ffprobe", ffmpeg_bin: str = "ffmpeg",
                     ltc_sekunden: float = 10.0) -> AssetProbe:
    p = Path(pfad)
    groesse = p.stat().st_size
    info = ffprobe_json(str(p), ffprobe_bin)
    streams = info.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), {})
    aus = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = info.get("format", {})
    dauer = float(fmt.get("duration") or vs.get("duration") or 0)
    fps = _fps_aus_stream(vs)
    fps_int = int(round(fps)) if fps else None
    kanaele = sum(int(s.get("channels") or 0) for s in aus)
    tags = fmt.get("tags", {}) or {}
    container_tc = tags.get("timecode")
    if not container_tc:
        for s in streams:
            t = (s.get("tags") or {}).get("timecode")
            if t:
                container_tc = t
                break
    warn: list[str] = []
    ltc_kanal = scratch_kanal = None
    tc_start = tc_s = tc_rate = tc_flag = None
    tc_quelle = "keine"

    if aus and kanaele > 0:
        # Kanalzählung: pan=c<n> zählt über den ERSTEN Audiostream; bei mehreren Streams
        # nur den ersten analysieren (Korpus: 1 Stream × 4 Kanäle).
        n = int(aus[0].get("channels") or 0)
        befund = finde_ltc_in_video(str(p), n, fps=fps_int, sekunden=ltc_sekunden, ffmpeg_bin=ffmpeg_bin)
        if befund.kanal is not None and befund.ergebnis and befund.ergebnis.gueltig:
            e = befund.ergebnis
            ltc_kanal = befund.kanal
            tc_quelle = "ltc"
            tc_start = e.tc_start
            tc_s = e.tc_start_seconds
            tc_rate = f"{e.fps}/1" if e.fps else None
            tc_flag = "DF" if e.drop_frame else "NDF"
        warn += [w for w in befund.warnungen if not w.startswith("kein dekodierbarer")]
        for s in befund.statistiken:
            if s.kanal != ltc_kanal and not s.stille and not s.ltc_kandidat:
                scratch_kanal = s.kanal
                break
        if len(aus) > 1:
            warn.append(f"{len(aus)} Audiostreams — nur Stream 0 auf LTC/Scratch geprüft")
    else:
        warn.append("Video ohne Audiospur — weder LTC noch Scratch möglich")

    if tc_quelle == "keine" and container_tc:
        # Vorläufig übernehmen; der Import verwirft identische Werte über ≥ 3 Dateien.
        try:
            r = Fraction(int(round(fps))) if fps else Fraction(24)
            tc_s = tc_zu_sekunden(container_tc, r)
            tc_start = container_tc
            tc_quelle = "container"
            tc_rate = f"{r}"
            tc_flag = "DF" if ";" in container_tc else "NDF"
        except (ValueError, TypeError):
            warn.append(f"Container-Timecode unlesbar: {container_tc!r}")

    if tc_quelle == "keine":
        warn.append("Kein Timecode (weder LTC noch brauchbarer Container-Tag)")

    return AssetProbe(
        typ="video", pfad=str(p), dateiname=p.name, dauer_s=round(dauer, 6),
        sample_rate=int(aus[0].get("sample_rate")) if aus and aus[0].get("sample_rate") else None,
        kanaele=kanaele or None, fps=fps, codec=vs.get("codec_name"), dateigroesse=groesse,
        tc_start=tc_start, tc_start_s=tc_s, tc_quelle=tc_quelle, tc_rate=tc_rate, tc_flag=tc_flag,
        ixml_json=None, fingerprint=fingerprint(p), namen=parse_video_name(p.name),
        datum=parse_datum_aus_dateiname(p.name) or _datum_aus_creation_time(tags, info),
        ltc_kanal=ltc_kanal, scratch_kanal=scratch_kanal,
        container_tc=container_tc, warnungen=warn,
    )


def _datum_aus_creation_time(tags: dict, info: dict) -> Optional[date]:
    """Drehtag aus dem Container-`creation_time` (Format- oder Stream-Tags). Das absolute Datum der
    Kamera-Uhr kann falsch gestellt sein — für die Drehtag-GRUPPIERUNG (matcher.drehtag_rang) zählt
    nur, dass Dateien desselben Tages dasselbe Datum tragen."""
    ct = (tags or {}).get("creation_time")
    if not ct:
        for s in info.get("streams", []) or []:
            ct = ((s.get("tags") or {}).get("creation_time"))
            if ct:
                break
    if ct and re.match(r"^\d{4}-\d{2}-\d{2}", str(ct)):
        try:
            return date.fromisoformat(str(ct)[:10])
        except ValueError:
            return None
    return None


def verwerfe_identische_container_tc(probes: Iterable[AssetProbe]) -> int:
    """Container-TC-Tag ist auf ≥ 3 Dateien identisch → Export-Artefakt → `tc_quelle = keine`."""
    probes = list(probes)
    zaehler: dict[str, int] = {}
    for pr in probes:
        if pr.container_tc:
            zaehler[pr.container_tc] = zaehler.get(pr.container_tc, 0) + 1
    verworfen = 0
    for pr in probes:
        if pr.container_tc and zaehler.get(pr.container_tc, 0) >= CONTAINER_TC_MAX_GLEICH:
            note = (f"Container-Timecode {pr.container_tc} auf {zaehler[pr.container_tc]} Dateien identisch "
                    f"— Export-Artefakt, verworfen")
            if pr.tc_quelle == "container":
                pr.tc_quelle = "keine"
                pr.tc_start = None
                pr.tc_start_s = None
                pr.tc_rate = None
                pr.tc_flag = None
                if "Kein Timecode (weder LTC noch brauchbarer Container-Tag)" not in pr.warnungen:
                    pr.warnungen.append("Kein Timecode (weder LTC noch brauchbarer Container-Tag)")
            if note not in pr.warnungen:
                pr.warnungen.append(note)
            verworfen += 1
    return verworfen
