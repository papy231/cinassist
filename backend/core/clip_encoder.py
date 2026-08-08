"""Geteilter CLIP-Encoder — EINZIGE Quelle der Wahrheit für Bild- und Text-Embeddings.

Ingest (Bild) und Suche/Retrieval (Text) MÜSSEN dasselbe Modell verwenden, sonst
liegen die Vektoren in unterschiedlichen Räumen/Dimensionen. Modell + Checkpoint
kommen aus der Config (CLIP_MODEL, CLIP_PRETRAINED). Lazy-Singleton (einmal geladen).
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("cinassist.clip_encoder")

_model = None
_tokenizer = None
_preprocess = None
_device = None


def _load() -> None:
    global _model, _tokenizer, _preprocess, _device
    if _model is not None:
        return
    import torch
    import open_clip
    from backend.core.config import CLIP_MODEL, CLIP_PRETRAINED

    _device = "mps" if torch.backends.mps.is_available() else "cpu"
    logger.info(f"Loading CLIP {CLIP_MODEL} / {CLIP_PRETRAINED} on {_device}…")
    model, _, preprocess = open_clip.create_model_and_transforms(
        CLIP_MODEL, pretrained=CLIP_PRETRAINED, device=_device
    )
    model.eval()
    _model = model
    _preprocess = preprocess
    _tokenizer = open_clip.get_tokenizer(CLIP_MODEL)
    logger.info("CLIP-Encoder bereit.")


def get_device() -> str:
    _load()
    return _device


def embed_dim() -> int:
    _load()
    import torch
    with torch.no_grad():
        t = _tokenizer(["x"]).to(_device)
        return int(_model.encode_text(t).shape[-1])


def embed_text(query: str) -> np.ndarray:
    """L2-normalisiertes Text-Embedding (für Query/Retrieval)."""
    _load()
    import torch
    tokens = _tokenizer([query or ""]).to(_device)
    with torch.no_grad():
        emb = _model.encode_text(tokens)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy()[0].astype(np.float32)


def embed_image(path) -> np.ndarray:
    """L2-normalisiertes Bild-Embedding eines einzelnen Frames."""
    _load()
    import torch
    from PIL import Image
    img = _preprocess(Image.open(str(path)).convert("RGB")).unsqueeze(0).to(_device)
    with torch.no_grad():
        emb = _model.encode_image(img)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy()[0].astype(np.float32)


def embed_images_mean(paths) -> "np.ndarray | None":
    """Mittelwert (L2-normalisiert) mehrerer Frame-Embeddings einer Szene.

    Mehrere Frames statt nur des Mittel-Frames machen das Szenen-Embedding
    robuster (ein unrepräsentativer Frame — z.B. Bewegungsunschärfe — verzerrt
    weniger). Gibt None zurück, wenn kein Frame erfolgreich eingebettet wurde.
    """
    _load()
    embs = []
    for p in paths:
        try:
            embs.append(embed_image(p))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Frame-Embedding fehlgeschlagen ({p}): {e}")
    if not embs:
        return None
    m = np.mean(np.stack(embs, axis=0), axis=0)
    n = float(np.linalg.norm(m))
    if n > 0:
        m = m / n
    return m.astype(np.float32)
