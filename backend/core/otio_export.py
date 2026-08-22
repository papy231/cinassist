"""
CinAssist — Génération OpenTimelineIO (FCPXML, OTIO JSON, EDL si dispo).

Nimmt eine Liste von Segmenten {clip_id, clip_path, media_start, duration, ...}
und erzeugt eine .fcpxml- oder .otio-Datei, die sich in Premiere Pro, Final Cut
Pro X, DaVinci Resolve, Autodesk Flame, etc.

Dieser Baustein macht CinAssist für Berufsschnitt brauchbar:
die gewohnte Schnittsoftware bleibt, der Rohschnitt wird eingelesen.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal

import opentimelineio as otio
from opentimelineio.opentime import RationalTime, TimeRange

logger = logging.getLogger("cinassist.otio_export")

# ~/Movies statt ~/Documents: Documents ist iCloud-synchronisiert — bei vollem iCloud
# werden Dateien evakuiert („Fehler“-Badge) und Resolve kann sie nicht lesen.
EXPORT_DIR = Path.home() / "Movies" / "CinAssist_Exports"

# Formats supportés → (extension, adapter_name)
FORMATS: dict[str, tuple[str, str]] = {
    "fcpxml": (".fcpxml", "fcpx_xml"),
    "otio": (".otio", "otio_json"),
    "edl": (".edl", "cmx_3600"),
}


def _build_timeline(
    segments: list[dict],
    name: str,
    fps: float = 30.0,
) -> otio.schema.Timeline:
    """
    Baut aus einer Liste von Segmenten eine OTIO-Zeitleiste.

    Jedes Segment muss enthalten:
      - clip_path (str): absoluter Pfad zur Ausgangsdatei
      - clip_name (str): lesbarer Name des Clips
      - media_start (float): Versatz in der Quelle, in Sekunden
      - duration (float): Dauer des Segments, in Sekunden
      - track (str, optionnel) : "v1", "v2"... défaut "v1"
    """
    timeline = otio.schema.Timeline(name=name)

    # Segmente nach Spur gruppieren
    by_track: dict[str, list[dict]] = {}
    for seg in segments:
        tr = seg.get("track", "v1").lower()
        by_track.setdefault(tr, []).append(seg)

    for track_name in sorted(by_track.keys()):
        track = otio.schema.Track(
            name=track_name.upper(),
            kind=otio.schema.TrackKind.Video,
        )
        timeline.tracks.append(track)

        for seg in by_track[track_name]:
            path = seg["clip_path"]
            duration_s = float(seg["duration"])
            media_start_s = float(seg.get("media_start", 0.0))

            # ExternalReference verweist auf die Ausgangsdatei
            media_ref = otio.schema.ExternalReference(
                target_url=f"file://{path}",
                available_range=TimeRange(
                    start_time=RationalTime(0, fps),
                    duration=RationalTime(duration_s * fps, fps),
                ),
            )

            clip = otio.schema.Clip(
                name=seg.get("clip_name", Path(path).stem),
                media_reference=media_ref,
                source_range=TimeRange(
                    start_time=RationalTime(media_start_s * fps, fps),
                    duration=RationalTime(duration_s * fps, fps),
                ),
            )
            track.append(clip)

    return timeline


def export_to_file(
    segments: list[dict],
    format: Literal["fcpxml", "otio", "edl"],
    name: str = "CinAssist_Timeline",
    fps: float = 30.0,
) -> dict:
    """
    Erzeugt eine NLE-Austauschdatei und speichert sie in ~/Documents/CinAssist_Exports/.

    Gibt ein Dict {path, format, size_bytes, segment_count, name} zurück.
    """
    if format not in FORMATS:
        raise ValueError(
            f"Unbekanntes Format: {format}. Verfügbar: {list(FORMATS.keys())}"
        )
    if not segments:
        raise ValueError("Kein Segment übergeben.")

    ext, adapter = FORMATS[format]
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in name if c.isalnum() or c in "_-") or "timeline"
    out_path = EXPORT_DIR / f"{safe_name}_{timestamp}{ext}"

    timeline = _build_timeline(segments, name=name, fps=fps)
    otio.adapters.write_to_file(timeline, str(out_path), adapter_name=adapter)

    logger.info(
        f"Export {format} → {out_path.name} "
        f"({out_path.stat().st_size} bytes, {len(segments)} segments)"
    )
    return {
        "path": str(out_path),
        "format": format,
        "adapter": adapter,
        "size_bytes": out_path.stat().st_size,
        "segment_count": len(segments),
        "name": name,
        "fps": fps,
    }
