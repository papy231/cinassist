"""
CinAssist — Rendu MP4 direct (Vagues 4.1 + 4.2).

Pour les vidéomonteurs qui veulent un livrable final (pas juste un FCPXML) :
    - Rough cut → MP4 dans un aspect ratio donné (16:9, 9:16, 1:1)
    - Optional : sous-titres burnt-in depuis Whisper transkription_json

Utilise FFmpeg directement (sans Celery, synchrone). Latence ~1s / seconde
de sortie sur M4 avec libx264 preset ultrafast.

Format segment attendu :
    {clip_path, clip_name, media_start, duration}

Aspect ratios :
    - "16:9" : crop centré ou passthrough si source déjà 16:9
    - "9:16" : crop centré vertical (mobile portrait)
    - "1:1"  : crop centré carré (Instagram feed)
"""
from __future__ import annotations

import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal

logger = logging.getLogger("cinassist.render")

from backend.core.config import TEMP_DIR

EXPORT_DIR = Path.home() / "Documents" / "CinAssist_Exports"

ASPECT_FILTERS = {
    "16:9": "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
    "9:16": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
    "1:1":  "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080",
}


def _tmp_dir() -> Path:
    d = TEMP_DIR / f"cinassist_render_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _has_audio(clip_path: str | Path) -> bool:
    """True si le fichier source contient au moins une piste audio (via ffprobe)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(clip_path)],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and "audio" in (r.stdout or "").lower()
    except Exception:
        return False


def _dump_stderr(prefix: str, stderr: str) -> Path:
    """Sauve stderr complet dans /tmp et retourne le path."""
    p = TEMP_DIR / f"cinassist_ffmpeg_{prefix}_{uuid.uuid4().hex[:8]}.log"
    p.write_text(stderr or "", encoding="utf-8", errors="replace")
    return p


def _extract_segment(seg: dict, aspect: str, subtitle_path: Path | None, out_path: Path) -> None:
    """Extrait un segment avec crop + optional subtitles burnt.

    Normalise l'audio : si le clip source n'a pas de piste audio, on en génère
    une (silence AAC) via anullsrc. Cela permet au concat -c copy de fonctionner
    quand on mélange des segments avec/sans audio (cause du bug #3 du rapport nocturne).
    """
    vf_chain = [ASPECT_FILTERS[aspect]]
    if subtitle_path and subtitle_path.exists():
        vf_chain.append(f"subtitles={str(subtitle_path).replace(':', '\\:')}")
    vf = ",".join(vf_chain)

    has_audio = _has_audio(seg["clip_path"])
    cmd = ["ffmpeg", "-y"]
    if has_audio:
        cmd += [
            "-ss", str(seg["media_start"]),
            "-i", seg["clip_path"],
            "-t", str(seg["duration"]),
        ]
    else:
        cmd += [
            "-ss", str(seg["media_start"]),
            "-i", seg["clip_path"],
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", str(seg["duration"]),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
        ]
    cmd += [
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-avoid_negative_ts", "make_zero",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        log_path = _dump_stderr("segment", r.stderr)
        logger.error("ffmpeg segment failed (has_audio=%s): full stderr in %s", has_audio, log_path)
        tail = (r.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError(
            f"ffmpeg segment failed (has_audio={has_audio}); full log: {log_path}; "
            f"last lines: {' | '.join(tail)}"
        )


def _concat_segments(segment_paths: list[Path], out_path: Path) -> None:
    """Concat multiple .mp4 en un seul via ffmpeg concat demuxer.

    Fallback : si -c copy échoue (streams incompatibles malgré la normalisation),
    on re-encode. Plus lent mais garanti de marcher.
    """
    concat_list = out_path.with_suffix(".concat.txt")
    concat_list.write_text("\n".join(f"file '{p}'" for p in segment_paths))
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        log_path = _dump_stderr("concat_copy", r.stderr)
        logger.warning("ffmpeg concat -c copy failed, retrying with re-encode. Log: %s", log_path)
        cmd_reencode = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            str(out_path),
        ]
        r = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=600)
    concat_list.unlink(missing_ok=True)
    if r.returncode != 0:
        log_path = _dump_stderr("concat_reencode", r.stderr)
        tail = (r.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError(
            f"ffmpeg concat failed even with re-encode; full log: {log_path}; "
            f"last lines: {' | '.join(tail)}"
        )


def _srt_from_whisper_segments(
    speech_segments: list[dict],
    time_offset: float = 0.0,
) -> str:
    """Convertit Whisper segments → SRT string. time_offset décale les timings."""
    def fmt(t: float) -> str:
        t = max(0.0, t)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    for i, seg in enumerate(speech_segments, start=1):
        start = float(seg.get("start", 0.0)) + time_offset
        end = float(seg.get("end", start + 1.0)) + time_offset
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def render_mp4(
    segments: list[dict],
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9",
    name: str = "CinAssist_Render",
    subtitles_srt: str | None = None,
) -> dict:
    """
    Rend une liste de segments en un MP4 unique.

    Args:
        segments: [{clip_path, clip_name, media_start, duration}, ...]
        aspect_ratio: 16:9 | 9:16 | 1:1
        name: nom du fichier de sortie
        subtitles_srt: contenu SRT complet à burn-in (None = pas de subs)

    Returns:
        {path, size_bytes, duration_s, aspect_ratio, segment_count, elapsed_s}
    """
    if not segments:
        raise ValueError("no segments to render")
    if aspect_ratio not in ASPECT_FILTERS:
        raise ValueError(f"aspect_ratio must be one of {list(ASPECT_FILTERS)}")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in name if c.isalnum() or c in "_-") or "render"
    out_path = EXPORT_DIR / f"{safe_name}_{aspect_ratio.replace(':','x')}_{timestamp}.mp4"

    t0 = time.time()
    tmp = _tmp_dir()
    srt_path = None
    if subtitles_srt:
        srt_path = tmp / "subs.srt"
        srt_path.write_text(subtitles_srt, encoding="utf-8")

    seg_paths: list[Path] = []
    try:
        for i, seg in enumerate(segments):
            p = tmp / f"seg_{i:04d}.mp4"
            _extract_segment(seg, aspect_ratio, srt_path, p)
            seg_paths.append(p)
        _concat_segments(seg_paths, out_path)
    finally:
        # cleanup
        for p in seg_paths:
            p.unlink(missing_ok=True)
        if srt_path:
            srt_path.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass

    elapsed = time.time() - t0
    return {
        "path": str(out_path),
        "size_bytes": out_path.stat().st_size,
        "duration_s": round(sum(s["duration"] for s in segments), 2),
        "aspect_ratio": aspect_ratio,
        "segment_count": len(segments),
        "elapsed_s": round(elapsed, 1),
        "has_subtitles": bool(subtitles_srt),
    }


# ─── Détection BPM / beats (V4.3) ────────────────────────────
def detect_beats(clip_path: str | Path) -> dict:
    """Détecte BPM et beat timestamps via librosa. Retourne {tempo, beat_times, count}."""
    try:
        import librosa
        import numpy as np
    except ImportError as e:
        return {"error": f"librosa not installed: {e}"}
    try:
        # Force ffmpeg extraction pour ne pas dépendre du décodeur audio de librosa
        tmp_audio = TEMP_DIR / f"beats_{uuid.uuid4().hex[:8]}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(clip_path), "-vn", "-ac", "1",
             "-ar", "22050", "-f", "wav", str(tmp_audio)],
            capture_output=True, timeout=60,
        )
        if not tmp_audio.exists() or tmp_audio.stat().st_size < 1000:
            return {"error": "audio extraction failed (no audio in clip?)"}
        y, sr = librosa.load(str(tmp_audio), sr=22050, mono=True)
        tmp_audio.unlink(missing_ok=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        # librosa 0.11+ retourne tempo comme np.ndarray 0-d → asarray().item() gère les 2 cas
        tempo_scalar = float(np.asarray(tempo).item()) if np.asarray(tempo).ndim == 0 else float(np.asarray(tempo).flat[0])
        return {
            "tempo_bpm": round(tempo_scalar, 1),
            "beat_count": int(len(beat_times)),
            "beat_times_s": [round(float(t), 3) for t in beat_times.tolist()],
            "duration_analyzed_s": round(len(y) / sr, 2),
        }
    except Exception as e:
        logger.exception("detect_beats failed")
        return {"error": f"beat detection failed: {e}"}
