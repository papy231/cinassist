"""
CinAssist — Export Worker
FFmpeg xfade / filter_complex für Multi-Clip-Export mit Übergängen.
"""
import json
import logging
import subprocess
import uuid
from pathlib import Path

from backend.core.celery_app import celery_app
from backend.core.config import FFMPEG_BIN, OUTPUT_DIR
from backend.core.database import SyncSessionLocal, Clip, Job

logger = logging.getLogger("cinassist.export")


# ─── Job-Hilfsfunktion ──────────────────────────────────

def _update_job(job_id: str, status: str, fortschritt: int, nachricht: str, ergebnis: dict | None = None):
    db = SyncSessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = status
            job.fortschritt = fortschritt
            job.nachricht = nachricht
            if ergebnis is not None:
                job.ergebnis = ergebnis
            db.commit()
    finally:
        db.close()

    try:
        import redis
        from backend.core.config import REDIS_URL
        r = redis.from_url(REDIS_URL)
        r.publish(f"job:{job_id}", json.dumps({
            "status": status,
            "progress": fortschritt,
            "message": nachricht,
            "result": ergebnis,
        }))
    except Exception:
        pass


def _resolve_clips(segments: list[dict]) -> dict[str, str]:
    """clip_id → absoluter Dateipfad"""
    ids = list({s["clip_id"] for s in segments if s.get("clip_id")})
    if not ids:
        return {}
    db = SyncSessionLocal()
    try:
        clips = db.query(Clip).filter(Clip.id.in_(ids)).all()
        return {str(c.id): str(c.dateipfad) for c in clips}
    finally:
        db.close()


# ─── FFmpeg Command Builder ──────────────────────────────

def _build_ffmpeg_cmd(v_segs: list[dict], file_map: dict[str, str],
                      output_path: Path, resolution: str = "1920x1080") -> list[str]:
    """
    Baut einen FFmpeg-Befehl mit xfade-Übergängen.

    Jedes Segment wird als eigenständiger Input mit -ss/-t eingelesen.
    Video: xfade-Kette (duration=0.001 für harte Schnitte ohne Übergang)
    Audio: acrossfade-Kette (gespiegelt zu xfade, hält Sync aufrecht)
    """
    w, h = ("1920", "1080")
    if "x" in resolution:
        parts = resolution.lower().split("x")
        w, h = parts[0], parts[1]

    scale_f = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1"
    )

    n = len(v_segs)
    if n == 0:
        return []

    # ─── Inputs: -ss/-t -i file für jedes Segment ────────
    cmd: list[str] = [FFMPEG_BIN, "-y"]
    for seg in v_segs:
        ms = float(seg.get("mediaStart", seg.get("media_start", 0)) or 0)
        dur = float(seg["dauer"])
        fp = file_map[seg["clip_id"]]
        cmd += ["-ss", f"{ms:.3f}", "-t", f"{dur:.3f}", "-i", fp]

    if n == 1:
        # Einzelnes Segment: einfacher Passthrough
        fc = f"[0:v]{scale_f}[vout];[0:a]asetpts=PTS-STARTPTS[aout]"
        cmd += ["-filter_complex", fc,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path)]
        return cmd

    # ─── Filter-Complex aufbauen ─────────────────────────
    filter_parts: list[str] = []

    # Jeden Stream normalisieren (fps=25 für konsistenten Framerate vor xfade)
    for i in range(n):
        filter_parts.append(f"[{i}:v]{scale_f},fps=25,setpts=PTS-STARTPTS[v{i}]")
        filter_parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")

    current_v = "v0"
    current_a = "a0"
    cumulative_offset = 0.0

    for i in range(1, n):
        seg = v_segs[i]
        trans = seg.get("transition") or {}
        has_trans = bool(trans and float(trans.get("dauer", 0)) > 0)

        prev_dur = float(v_segs[i - 1]["dauer"])
        next_dur = float(seg["dauer"])

        # Sicherheitsmarge: trans_dur maximal 40% des kürzeren Segments,
        # minus 2 Frames (0.08s bei 25fps) damit xfade nie über das Ende liest.
        FRAME_SAFETY = 0.08
        max_safe_trans = min(prev_dur, next_dur) * 0.40 - FRAME_SAFETY

        if has_trans:
            trans_dur = min(float(trans.get("dauer", 0.0)), max_safe_trans)
            trans_dur = max(trans_dur, 0.04)   # Mindestens 1 echter Frame
        else:
            trans_dur = 0.001  # Harter Schnitt
        trans_type = trans.get("type", "dissolve") if has_trans and trans_dur >= 0.04 else "dissolve"

        # Offset = kumulative Ausgabeposition, an der dieser Übergang beginnt
        cumulative_offset += prev_dur - trans_dur
        cumulative_offset = max(0.001, cumulative_offset)  # nie negativ

        v_next = f"xv{i}" if i < n - 1 else "vout"
        a_next = f"xa{i}" if i < n - 1 else "aout"

        filter_parts.append(
            f"[{current_v}][v{i}]xfade="
            f"transition={trans_type}:duration={trans_dur:.4f}:offset={cumulative_offset:.4f}"
            f"[{v_next}]"
        )
        filter_parts.append(
            f"[{current_a}][a{i}]acrossfade=d={trans_dur:.4f}[{a_next}]"
        )
        current_v = v_next
        current_a = a_next

    filter_complex = ";".join(filter_parts)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd


# ─── Celery Task ────────────────────────────────────────

@celery_app.task(bind=True, name="backend.workers.export.export_video_task")
def export_video_task(self, job_id: str, export_data: dict):
    """Export-Task: Timeline → MP4 mit FFmpeg."""
    logger.info(f"Export gestartet: job={job_id}")
    _update_job(job_id, "laeuft", 5, "Export wird vorbereitet…")

    try:
        segments: list[dict] = export_data.get("segments", [])
        resolution: str = export_data.get("resolution", "1920x1080")

        if not segments:
            _update_job(job_id, "fehler", 0, "Keine Segmente angegeben")
            return

        # Nur Videosegmente
        v_segs = sorted(
            [s for s in segments if s.get("track", "").startswith("v")],
            key=lambda s: s["start"],
        )
        if not v_segs:
            _update_job(job_id, "fehler", 0, "Keine Videosegmente in der Timeline")
            return

        _update_job(job_id, "laeuft", 15, "Dateipfade werden aufgelöst…")
        file_map = _resolve_clips(v_segs)

        # Prüfen ob alle Clips vorhanden
        missing = [s["clip_id"] for s in v_segs if s.get("clip_id") not in file_map]
        if missing:
            _update_job(job_id, "fehler", 0, f"{len(missing)} Clip(s) nicht gefunden")
            return

        output_filename = f"export_{job_id[:8]}.mp4"
        output_path = OUTPUT_DIR / output_filename

        _update_job(job_id, "laeuft", 25, "FFmpeg-Befehl wird erstellt…")
        cmd = _build_ffmpeg_cmd(v_segs, file_map, output_path, resolution)
        if not cmd:
            _update_job(job_id, "fehler", 0, "Fehler beim Erstellen des FFmpeg-Befehls")
            return

        logger.info(f"FFmpeg ({len(v_segs)} Segmente): {' '.join(cmd[:15])}…")
        _update_job(job_id, "laeuft", 35, f"FFmpeg läuft ({len(v_segs)} Segmente)…")

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if proc.returncode != 0:
            stderr_tail = proc.stderr[-600:] if proc.stderr else "Kein Fehlertext"
            logger.error(f"FFmpeg Fehler: {stderr_tail}")
            _update_job(job_id, "fehler", 0, f"FFmpeg Fehler: {stderr_tail[:200]}")
            return

        output_url = f"/outputs/{output_filename}"
        _update_job(job_id, "fertig", 100, "Export abgeschlossen! 🎬", {
            "output_url": output_url,
            "output_filename": output_filename,
            "segment_count": len(v_segs),
        })
        logger.info(f"Export fertig: {output_url}")

    except subprocess.TimeoutExpired:
        _update_job(job_id, "fehler", 0, "FFmpeg Timeout (>10 Minuten)")
    except Exception as e:
        logger.exception(f"Export Fehler: {e}")
        _update_job(job_id, "fehler", 0, f"Unerwarteter Fehler: {str(e)[:200]}")
