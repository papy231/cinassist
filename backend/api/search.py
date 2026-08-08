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

import json
import re

import httpx
import numpy as np
import open_clip
import torch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import OLLAMA_BASE_URL
from backend.core.database import Szene, get_db

# Query rewriting : llama3 enrichit la query utilisateur avant embedding CLIP.
# "animal" → "a cartoon animal character, close-up, expressive face" — CLIP
# fonctionne mieux avec des descriptions riches en attributs visuels.
REWRITE_MODEL = "llama3"
_REWRITE_CACHE: dict[str, str] = {}  # simple mémoire in-process (max ~100 entries en pratique)


async def _rewrite_query(query: str) -> str:
    """Retourne une version enrichie de la query pour l'embedding CLIP.

    En cas d'erreur (Ollama down, LLM timeout, parse fail), retourne la query
    originale — la recherche continue de marcher, juste moins précise.
    """
    q = query.strip()
    if not q or len(q) > 200:
        return q
    if q in _REWRITE_CACHE:
        return _REWRITE_CACHE[q]

    prompt = (
        "Erweitere die folgende Video-Suchanfrage zu einer präziseren visuellen Beschreibung "
        "für CLIP (2-4 zusätzliche visuelle Attribute wie Framing, Beleuchtung, Aktion, Stimmung). "
        "Behalte die Originalintention. Antworte NUR im JSON-Format {\"expanded\": \"...\"}, "
        "auf Englisch (CLIP bevorzugt Englisch).\n\n"
        f"Anfrage: {q}\n"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": REWRITE_MODEL, "prompt": prompt, "format": "json", "stream": False, "options": {"temperature": 0.4}},
            )
            r.raise_for_status()
            data = r.json()
        parsed = json.loads(data.get("response", "{}"))
        expanded = str(parsed.get("expanded", "")).strip()
        if expanded and len(expanded) < 400:
            # Concaténer avec la query originale pour préserver les mots exacts
            # (utile pour BM25 côté frontend même si non-utilisé ici).
            combined = f"{q}. {expanded}"
            _REWRITE_CACHE[q] = combined
            return combined
    except Exception as exc:
        logger.info(f"Query rewriting failed for «{q[:40]}»: {exc} — fallback plain")
    _REWRITE_CACHE[q] = q
    return q

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
    # Délègue à l'encodeur partagé (core/clip_encoder) → même modèle que l'ingest.
    from backend.core import clip_encoder
    return clip_encoder.embed_text(query)


# ─── Schemas ─────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Prompt in natural language")
    limit: int = Field(10, ge=1, le=100)
    clip_ids: list[str] | None = Field(
        None, description="Optional: restrict search to these clip IDs"
    )
    min_similarity: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Minimum combined score (0.0-1.0). Typically 0.15-0.30 is meaningful.",
    )
    # Hybride : pondération entre visuel (CLIP) et texte (BM25 sur transkription
    # + beschreibung). Somme = 1.0. Défaut 0.65/0.35 favorise visuel mais garde
    # le texte pertinent pour "dialog", "brief", noms propres, etc.
    weight_clip: float = Field(0.65, ge=0.0, le=1.0)
    weight_text: float = Field(0.35, ge=0.0, le=1.0)
    # Query rewriting : llama3 enrichit la requête avant embedding CLIP. +5-15
    # points de similarity typique. Coût ~1-2s au premier appel (cache après).
    rewrite: bool = Field(True, description="Aktiviert LLM-basiertes Query-Rewriting vor CLIP-Embedding.")


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
    similarity: float           # score combiné final
    clip_score: float           # composante visuelle CLIP (cosine)
    text_score: float           # composante texte BM25 (normalisée 0-1)
    framing: str | None = None  # closeup/medium/wide… pour filtres inline
    face_count: int | None = None


class SearchResponse(BaseModel):
    query: str
    query_rewritten: str | None = None   # version enrichie utilisée pour l'embedding
    results: list[SearchResult]
    count: int
    scanned: int
    elapsed_ms: float


# ─── Endpoint ────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def _tokenize(text: str | None) -> list[str]:
    """Tokenizer léger multilingue (DE/EN/FR) pour BM25 : mots alphanumériques
    minuscules, préserve umlauts. Zéro dépendance NLP.
    """
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


class SimilarByClipRequest(BaseModel):
    clip_id: str = Field(..., description="Source clip whose scenes' embeddings form the query vector.")
    scene_id: str | None = Field(None, description="Optional: use only this scene's embedding (else = mean of all scenes of the clip).")
    limit: int = Field(12, ge=1, le=50)
    min_similarity: float = Field(0.20, ge=0.0, le=1.0)
    exclude_source: bool = Field(True, description="Filtert die Quellclip-Szenen aus den Ergebnissen aus.")


@router.post("/similar-by-clip", response_model=SearchResponse)
async def similar_by_clip(
    req: SimilarByClipRequest, db: AsyncSession = Depends(get_db)
) -> SearchResponse:
    """Trouve des scènes visuellement similaires à celles d'un clip donné.

    Utilise le CLIP embedding déjà calculé lors de l'ingest — pas de rewriting
    ni de text encoder nécessaire. Ideal pour "trouve d'autres plans similaires
    à celui-ci" depuis la timeline.
    """
    t0 = time.time()

    # Charge les embeddings du clip source (+ relation clip pour label)
    src_stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(Szene.clip_id == req.clip_id)
        .where(Szene.clip_embedding.isnot(None))
    )
    if req.scene_id:
        src_stmt = src_stmt.where(Szene.id == req.scene_id)
    src_scenes = (await db.execute(src_stmt)).scalars().all()
    if not src_scenes:
        raise HTTPException(404, f"Keine Embeddings für clip_id={req.clip_id}.")

    # Vecteur query = moyenne des embeddings des scènes source (ou celui d'UNE scène)
    src_embs = np.array([s.clip_embedding for s in src_scenes], dtype=np.float32)
    if src_embs.shape[0] > 1:
        query_emb = src_embs.mean(axis=0)
    else:
        query_emb = src_embs[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    # Charge toutes les autres scènes
    tgt_stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(Szene.clip_embedding.isnot(None))
    )
    if req.exclude_source:
        tgt_stmt = tgt_stmt.where(Szene.clip_id != req.clip_id)
    scenes = (await db.execute(tgt_stmt)).scalars().all()
    if not scenes:
        return SearchResponse(
            query=f"[similar to clip {req.clip_id[:8]}]",
            results=[], count=0, scanned=0,
            elapsed_ms=(time.time() - t0) * 1000,
        )

    embs = np.array([s.clip_embedding for s in scenes], dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_normed = embs / norms
    sims = embs_normed @ query_emb

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
            scene_id=str(s.id), clip_id=str(s.clip_id),
            clip_name=s.clip.dateiname if s.clip else "",
            szenen_nr=s.szenen_nr, start_zeit=s.start_zeit, end_zeit=s.end_zeit, dauer=s.dauer,
            thumbnail_pfad=s.thumbnail_pfad, beschreibung=s.beschreibung, transkription=s.transkription,
            similarity=sim, clip_score=sim, text_score=0.0,
            framing=s.framing, face_count=s.face_count,
        ))

    src_name = src_scenes[0].clip.dateiname if src_scenes[0].clip else req.clip_id[:8]
    return SearchResponse(
        query=f"[similar to {src_name}]",
        query_rewritten=None,
        results=results,
        count=len(results),
        scanned=len(scenes),
        elapsed_ms=(time.time() - t0) * 1000,
    )


@router.post("/search", response_model=SearchResponse)
async def search_scenes(
    req: SearchRequest, db: AsyncSession = Depends(get_db)
) -> SearchResponse:
    """
    Recherche hybride : CLIP (visuel) + BM25 (texte sur beschreibung + transkription).

    - CLIP text encoder → cosine similarity contre `szene.clip_embedding` (512-D).
    - BM25 sur le corpus des textes disponibles par scène (description LLaMA3
      + transcription Whisper). Score normalisé [0..1] via max de la ronde.
    - Score final = w_clip * cosine + w_text * bm25_norm.

    Cosine ne trouve pas « was passiert im brief » (sémantique visuelle floue).
    BM25 récupère les scènes qui MENTIONNENT « brief » dans le transcript.
    """
    t0 = time.time()

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

    # ── Query rewriting (llama3 → attributs visuels enrichis) ──────
    query_for_embed = req.query
    query_rewritten: str | None = None
    if req.rewrite:
        expanded = await _rewrite_query(req.query)
        if expanded and expanded != req.query:
            query_for_embed = expanded
            query_rewritten = expanded

    # ── Composante CLIP (visuel) ────────────────────────────
    query_emb = _embed_text(query_for_embed)
    embs = np.array([s.clip_embedding for s in scenes], dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_normed = embs / norms
    clip_scores = embs_normed @ query_emb  # (N,) déjà L2-normed

    # ── Composante BM25 (texte : beschreibung + transkription) ──────
    corpus_tokens = [
        _tokenize((s.beschreibung or "") + " " + (s.transkription or ""))
        for s in scenes
    ]
    query_tokens = _tokenize(req.query)
    text_scores = np.zeros(len(scenes), dtype=np.float32)
    if query_tokens and any(corpus_tokens):
        # Filtre les scènes sans aucun token — BM25Okapi n'aime pas les vides.
        non_empty_idx = [i for i, toks in enumerate(corpus_tokens) if toks]
        if non_empty_idx:
            bm25 = BM25Okapi([corpus_tokens[i] for i in non_empty_idx])
            raw = bm25.get_scores(query_tokens)  # scores non normalisés
            max_raw = float(raw.max()) if raw.size > 0 else 0.0
            if max_raw > 0:
                for j, idx in enumerate(non_empty_idx):
                    text_scores[idx] = float(raw[j]) / max_raw

    # ── Score combiné ───────────────────────────────────────
    w_clip = req.weight_clip
    w_text = req.weight_text
    total_w = w_clip + w_text
    if total_w <= 0:
        w_clip, w_text, total_w = 0.65, 0.35, 1.0
    combined = (w_clip / total_w) * clip_scores + (w_text / total_w) * text_scores

    order = np.argsort(combined)[::-1]
    results: list[SearchResult] = []
    for idx in order:
        score = float(combined[idx])
        if score < req.min_similarity:
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
            similarity=score,
            clip_score=float(clip_scores[idx]),
            text_score=float(text_scores[idx]),
            framing=s.framing,
            face_count=s.face_count,
        ))

    return SearchResponse(
        query=req.query,
        query_rewritten=query_rewritten,
        results=results,
        count=len(results),
        scanned=len(scenes),
        elapsed_ms=(time.time() - t0) * 1000,
    )
