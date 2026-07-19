"""
CinAssist — CLIP Text Search

Endpoint POST /api/scenes/search : cherche des scènes par prompt en langage
naturel via le text encoder de CLIP ViT-B-32.

Fondation directe pour le tool `search_scenes_by_prompt` de l'agent ReAct
(Vague 1.4). Utile aussi côté frontend pour une barre de recherche libre.

Exemples de queries :
    - "wide drone shot at sunset"
    - "person talking in close-up"
    - "fast action with lots of movement"
    - "peaceful landscape without people"

Le text encoder est chargé une seule fois (lazy singleton) et gardé en RAM.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import open_clip
import torch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import Szene, get_db

logger = logging.getLogger("cinassist.search")
router = APIRouter(prefix="/api/scenes", tags=["Suche"])


# ─── CLIP text encoder (Lazy Singleton) ─────────────────────
_clip_model = None
_clip_tokenizer = None
_clip_device = None


def _get_clip():
    global _clip_model, _clip_tokenizer, _clip_device
    if _clip_model is None:
        _clip_device = "mps" if torch.backends.mps.is_available() else "cpu"
        logger.info(f"Loading CLIP ViT-B-32 text encoder on {_clip_device}...")
        model, _, _ = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai", device=_clip_device
        )
        model.eval()
        _clip_model = model
        _clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")
        logger.info("CLIP text encoder ready.")
    return _clip_model, _clip_tokenizer, _clip_device


def _embed_text(query: str) -> np.ndarray:
    model, tokenizer, device = _get_clip()
    tokens = tokenizer([query]).to(device)
    with torch.no_grad():
        emb = model.encode_text(tokens)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy()[0].astype(np.float32)


# ─── Schemas ─────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Prompt in natural language")
    limit: int = Field(10, ge=1, le=100)
    clip_ids: list[str] | None = Field(
        None, description="Optional: restrict search to these clip IDs"
    )
    min_similarity: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Minimum cosine similarity (0.0-1.0). Typically 0.15-0.30 is meaningful.",
    )


class SearchResult(BaseModel):
    scene_id: str
    clip_id: str
    clip_name: str
    szenen_nr: int
    start_zeit: float
    end_zeit: float
    dauer: float
    thumbnail_pfad: str | None
    beschreibung: str | None
    transkription: str | None
    similarity: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    count: int
    scanned: int
    elapsed_ms: float


# ─── Endpoint ────────────────────────────────────────────────
@router.post("/search", response_model=SearchResponse)
async def search_scenes(
    req: SearchRequest, db: AsyncSession = Depends(get_db)
) -> SearchResponse:
    """
    CLIP-based semantic search over all scenes that have a stored embedding.

    Cosine similarity is computed in Python with numpy (fine up to a few
    thousand scenes; migrate to pgvector if the project grows past ~10k).
    """
    t0 = time.time()

    query_emb = _embed_text(req.query)

    stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(Szene.clip_embedding.isnot(None))
    )
    if req.clip_ids:
        stmt = stmt.where(Szene.clip_id.in_(req.clip_ids))

    result = await db.execute(stmt)
    scenes = result.scalars().all()

    if not scenes:
        return SearchResponse(
            query=req.query, results=[], count=0, scanned=0,
            elapsed_ms=(time.time() - t0) * 1000,
        )

    embs = np.array(
        [s.clip_embedding for s in scenes], dtype=np.float32
    )  # (N, 512)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_normed = embs / norms
    sims = embs_normed @ query_emb  # (N,), query_emb already L2-normed

    order = np.argsort(sims)[::-1]
    results: list[SearchResult] = []
    for idx in order:
        sim = float(sims[idx])
        if sim < req.min_similarity:
            break
        if len(results) >= req.limit:
            break
        s = scenes[idx]
        results.append(SearchResult(
            scene_id=str(s.id),
            clip_id=str(s.clip_id),
            clip_name=s.clip.dateiname if s.clip else "",
            szenen_nr=s.szenen_nr,
            start_zeit=s.start_zeit,
            end_zeit=s.end_zeit,
            dauer=s.dauer,
            thumbnail_pfad=s.thumbnail_pfad,
            beschreibung=s.beschreibung,
            transkription=s.transkription,
            similarity=sim,
        ))

    return SearchResponse(
        query=req.query,
        results=results,
        count=len(results),
        scanned=len(scenes),
        elapsed_ms=(time.time() - t0) * 1000,
    )
