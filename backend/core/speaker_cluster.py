"""
CinAssist — Cross-Clip Speaker Clustering (Vague 5.2).

pyannote diarization gibt pro Clip einen eigenen SPEAKER_00, SPEAKER_01… ohne
Wissen über andere Clips. Wenn dieselbe Person in mehreren Clips vorkommt
(z. B. Interview mit 2 Wechsel-Aufnahmen), erscheint sie als 2 verschiedene
Speaker-IDs.

Dieses Modul berechnet pyannote-Voice-Embeddings pro Speaker über einen
Repräsentanten-Ausschnitt (längste ununterbrochene Rede) und gruppiert
per Cosine-Similarity-Clustering die Speaker cross-clip.

Ergebnis: Liste von Speaker-Clustern (jeder Cluster = eine Person).
"""
from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("cinassist.speaker_cluster")

_embedding_pipeline = None
_pipeline_error: str | None = None


def _get_embedding_pipeline():
    """Lazy load SpeechBrain ECAPA-TDNN voice embedding model (no HF license needed)."""
    global _embedding_pipeline, _pipeline_error
    if _embedding_pipeline is not None or _pipeline_error is not None:
        return _embedding_pipeline

    try:
        from speechbrain.inference.speaker import EncoderClassifier
        import torch
        from backend.core.config import TEMP_DIR

        savedir = str(TEMP_DIR / "speechbrain_ecapa")
        # CPU par défaut : SpeechBrain a plusieurs sous-modules (features,
        # normalizer, embedding) qui ne migrent pas ensemble sur MPS →
        # crash "input/weight device mismatch". CPU est acceptable ici
        # (~200ms par embedding sur M4, on est appelé rarement).
        classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=savedir,
            run_opts={"device": "cpu"},
        )
        _embedding_pipeline = classifier
        logger.info("SpeechBrain ECAPA-TDNN embedding model geladen.")
    except Exception as e:
        _pipeline_error = f"SpeechBrain embedding load fehlgeschlagen: {e}"
        logger.error(_pipeline_error)
    return _embedding_pipeline


def _extract_audio_segment(video_path: str, start_s: float, duration_s: float, temp_dir: Path) -> Path | None:
    """Extrahiert einen Audio-Ausschnitt als 16 kHz Mono WAV."""
    out = temp_dir / f"spk_{uuid.uuid4().hex[:8]}.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y",
         "-ss", str(start_s), "-t", str(duration_s),
         "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000",
         "-f", "wav", str(out)],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        return None
    return out


def _compute_embedding(audio_wav: Path):
    """Berechnet einen 192-dim Voice-Embedding via SpeechBrain ECAPA-TDNN."""
    import numpy as np
    import torch
    import torchaudio

    classifier = _get_embedding_pipeline()
    if classifier is None:
        raise RuntimeError(_pipeline_error or "kein embedding pipeline")

    waveform, sr = torchaudio.load(str(audio_wav))
    if sr != 16000:
        resampler = torchaudio.transforms.Resample(sr, 16000)
        waveform = resampler(waveform)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # Waveform sur CPU (voir _get_embedding_pipeline)
    waveform = waveform.cpu()

    with torch.no_grad():
        emb = classifier.encode_batch(waveform)
    return emb.squeeze().cpu().numpy()


def cluster_speakers(
    speakers_info: list[dict],
    temp_dir: Path,
    similarity_threshold: float = 0.75,
) -> dict:
    """
    Args:
        speakers_info: list of dicts with keys:
            speaker_id (str), clip_path (str), start_s (float), duration_s (float),
            label_auto (str), label_manual (str|None)
        temp_dir: temporary directory for audio extraction
        similarity_threshold: cosine similarity threshold [0..1] to merge speakers

    Returns:
        {
            "clusters": [
                {
                    "cluster_id": 0,
                    "speaker_ids": ["uuid1", "uuid2"],
                    "labels": ["SPEAKER_00 (mlk)", "SPEAKER_00 (snowden)"],
                    "suggested_label": "Speaker_A",
                    "avg_similarity": 0.82,
                },
                ...
            ],
            "unclustered": ["speaker_id_x", ...],  # embedding fehlgeschlagen
            "similarity_threshold": 0.75,
        }
    """
    import numpy as np

    if len(speakers_info) < 2:
        return {"error": "clustering benötigt mindestens 2 Speaker"}

    if _get_embedding_pipeline() is None:
        return {"error": _pipeline_error or "kein embedding pipeline"}

    embeddings: dict[str, "np.ndarray"] = {}
    unclustered: list[str] = []

    for sp in speakers_info:
        sid = sp["speaker_id"]
        try:
            audio = _extract_audio_segment(sp["clip_path"], sp["start_s"], sp["duration_s"], temp_dir)
            if not audio:
                unclustered.append(sid)
                continue
            try:
                emb = _compute_embedding(audio)
                embeddings[sid] = emb
            finally:
                audio.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Embedding für %s fehlgeschlagen: %s", sid, e)
            unclustered.append(sid)

    if len(embeddings) < 2:
        return {"error": f"zu wenige verwertbare Embeddings ({len(embeddings)})", "unclustered": unclustered}

    # Cosine similarity matrix
    sids = list(embeddings.keys())
    matrix = np.stack([embeddings[s] for s in sids])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    normed = matrix / (norms + 1e-9)
    sim = normed @ normed.T  # (N, N)

    # Union-Find pour merger paire à paire au-dessus du seuil
    parent = list(range(len(sids)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        pa, pb = find(a), find(b)
        if pa != pb: parent[pa] = pb

    for i in range(len(sids)):
        for j in range(i + 1, len(sids)):
            if float(sim[i, j]) >= similarity_threshold:
                union(i, j)

    # Gruppieren
    groups: dict[int, list[int]] = {}
    for i in range(len(sids)):
        root = find(i)
        groups.setdefault(root, []).append(i)

    lookup = {s["speaker_id"]: s for s in speakers_info}
    clusters = []
    for cluster_idx, (_, indices) in enumerate(groups.items()):
        member_ids = [sids[i] for i in indices]
        labels = []
        for sid in member_ids:
            info = lookup.get(sid, {})
            clip_name = info.get("clip_name", "?")
            lbl = info.get("label_manual") or info.get("label_auto", "?")
            labels.append(f"{lbl} ({clip_name})")

        # avg similarity innerhalb du cluster
        if len(indices) > 1:
            pair_sims = []
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    pair_sims.append(float(sim[indices[i], indices[j]]))
            avg = round(sum(pair_sims) / len(pair_sims), 3)
        else:
            avg = 1.0

        # Bevorzugter Label = erster label_manual, sonst erster label_auto
        preferred = None
        for sid in member_ids:
            info = lookup.get(sid, {})
            if info.get("label_manual"):
                preferred = info["label_manual"]
                break
        if not preferred:
            preferred = f"Speaker_{chr(65 + cluster_idx)}"

        clusters.append({
            "cluster_id": cluster_idx,
            "speaker_ids": member_ids,
            "speaker_count": len(member_ids),
            "labels": labels,
            "suggested_label": preferred,
            "avg_similarity": avg,
        })

    # Trie clusters : ceux avec plusieurs membres en premier
    clusters.sort(key=lambda c: (-c["speaker_count"], c["cluster_id"]))

    return {
        "clusters": clusters,
        "unclustered": unclustered,
        "similarity_threshold": similarity_threshold,
        "total_speakers_processed": len(embeddings),
    }
