"""
CinAssist — Speaker Diarization (Vague 1.2)

Utilise pyannote.audio 3.3.2 avec le pipeline pré-entraîné
`pyannote/speaker-diarization-3.1` pour identifier "qui parle quand".

⚠️ Nécessite un token HuggingFace + acceptation de la licence des modèles
sur huggingface.co/pyannote/speaker-diarization-3.1 et
huggingface.co/pyannote/segmentation-3.0

Le token est lu depuis :
    1. env var HUGGINGFACE_HUB_TOKEN
    2. fichier ~/.openclaw/workspace/.secrets/huggingface.json (clé "token")
    3. ~/.cache/huggingface/token (standard huggingface-cli login)

Résultat : liste de segments {start, end, speaker_label} (SPEAKER_00, SPEAKER_01, ...).
Ces labels auto sont ensuite renommés manuellement par l'utilisateur ("Anna",
"Marc") via un endpoint dédié.

Latence : ~5-15s pour 1 min d'audio sur M4 (MPS accelerated).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("cinassist.diarize")

_pipeline = None
_pipeline_error: str | None = None


def _resolve_hf_token() -> str | None:
    token = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN")
    if token:
        return token
    secret_file = Path.home() / ".openclaw/workspace/.secrets/huggingface.json"
    if secret_file.exists():
        try:
            data = json.loads(secret_file.read_text())
            t = data.get("token") or data.get("hf_token")
            if t:
                return t
        except Exception as e:
            logger.warning(f"Could not read {secret_file}: {e}")
    cli_token = Path.home() / ".cache/huggingface/token"
    if cli_token.exists():
        return cli_token.read_text().strip() or None
    return None


def _get_pipeline():
    """Lazy load pyannote pipeline. Retourne None si token/licence manquants."""
    global _pipeline, _pipeline_error
    if _pipeline is not None or _pipeline_error is not None:
        return _pipeline
    token = _resolve_hf_token()
    if not token:
        _pipeline_error = (
            "No HuggingFace token found. Set HUGGINGFACE_HUB_TOKEN or write to "
            "~/.openclaw/workspace/.secrets/huggingface.json"
        )
        logger.warning(_pipeline_error)
        return None
    try:
        from pyannote.audio import Pipeline
        import torch

        logger.info("Loading pyannote/speaker-diarization-3.1...")
        try:
            pipe = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=token,
            )
        except TypeError:
            # ancienne API < 0.24
            pipe = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=token,
            )
        if torch.backends.mps.is_available():
            pipe.to(torch.device("mps"))
        elif torch.cuda.is_available():
            pipe.to(torch.device("cuda"))
        _pipeline = pipe
        logger.info("pyannote pipeline ready.")
    except Exception as e:
        _pipeline_error = f"Failed to load pyannote pipeline: {e}"
        logger.error(_pipeline_error)
    return _pipeline


def diarize_audio(
    audio_path: str | Path,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    min_speaker_time_s: float = 3.0,
) -> dict:
    """
    Analyse un fichier audio (.wav mono 16kHz idéalement) et retourne
    la structure des interventions de chaque locuteur.

    Args:
        num_speakers: nombre exact de speakers attendus (interview 2 → passe 2).
                     Prioritaire sur min/max.
        min_speakers / max_speakers: bornes si `num_speakers` inconnu (ex: min=2, max=4).
        min_speaker_time_s: post-filter — retire les speakers avec un temps
                            de parole total inférieur à ce seuil (probables
                            faux positifs pyannote sur discours long).

    Retour :
        {
            "available": bool,
            "error": str | None,
            "speakers": ["SPEAKER_00", "SPEAKER_01", ...],
            "segments": [{"start": float, "end": float, "speaker": "SPEAKER_00"}, ...],
            "total_speakers": int,
            "filtered_out": int,          # nombre de speakers rejetés (< min_time)
            "hint_used": dict | None,     # rappel des hints appliqués
        }
    """
    pipe = _get_pipeline()
    if pipe is None:
        return {
            "available": False,
            "error": _pipeline_error or "pipeline not loaded",
            "speakers": [],
            "segments": [],
            "total_speakers": 0,
            "filtered_out": 0,
            "hint_used": None,
        }

    pipe_kwargs: dict = {}
    if num_speakers is not None and num_speakers > 0:
        pipe_kwargs["num_speakers"] = int(num_speakers)
    else:
        if min_speakers is not None and min_speakers > 0:
            pipe_kwargs["min_speakers"] = int(min_speakers)
        if max_speakers is not None and max_speakers > 0:
            pipe_kwargs["max_speakers"] = int(max_speakers)

    try:
        result = pipe(str(audio_path), **pipe_kwargs)
    except Exception as e:
        logger.exception("Diarization failed")
        return {
            "available": False,
            "error": f"diarization failed: {e}",
            "speakers": [],
            "segments": [],
            "total_speakers": 0,
            "filtered_out": 0,
            "hint_used": pipe_kwargs or None,
        }

    raw_segments: list[dict] = []
    times_by_speaker: dict[str, float] = {}
    for turn, _, label in result.itertracks(yield_label=True):
        dur = float(turn.end - turn.start)
        raw_segments.append({
            "start": round(turn.start, 3),
            "end": round(turn.end, 3),
            "speaker": label,
        })
        times_by_speaker[label] = times_by_speaker.get(label, 0.0) + dur

    # Post-filter : retire les speakers en dessous du seuil
    keep = {s for s, t in times_by_speaker.items() if t >= min_speaker_time_s}
    dropped = set(times_by_speaker) - keep
    filtered_segments = [s for s in raw_segments if s["speaker"] in keep]

    return {
        "available": True,
        "error": None,
        "speakers": sorted(keep),
        "segments": filtered_segments,
        "total_speakers": len(keep),
        "filtered_out": len(dropped),
        "hint_used": pipe_kwargs or None,
    }


def summarize_by_speaker(segments: list[dict]) -> dict[str, dict]:
    """Aggrège par speaker : {speaker_label: {total_time, segment_count, first_appearance}}."""
    agg: dict[str, dict] = {}
    for s in segments:
        lbl = s["speaker"]
        dur = s["end"] - s["start"]
        if lbl not in agg:
            agg[lbl] = {"total_time": 0.0, "segment_count": 0, "first_appearance": s["start"]}
        agg[lbl]["total_time"] += dur
        agg[lbl]["segment_count"] += 1
    return agg


def match_speakers_to_scenes(
    segments: list[dict],
    scene_ranges: list[tuple[float, float]],
) -> list[dict[str, float]]:
    """
    Pour chaque scène (start, end), retourne un dict {speaker_label: overlap_seconds}.

    Utile pour peupler la table scene_speakers.
    """
    out: list[dict[str, float]] = []
    for s_start, s_end in scene_ranges:
        by_speaker: dict[str, float] = {}
        for seg in segments:
            overlap_start = max(seg["start"], s_start)
            overlap_end = min(seg["end"], s_end)
            if overlap_end > overlap_start:
                dur = overlap_end - overlap_start
                by_speaker[seg["speaker"]] = by_speaker.get(seg["speaker"], 0.0) + dur
        out.append(by_speaker)
    return out
