"""
CinAssist — CLIP Text Search

Endpunkt POST /api/scenes/search: sucht Szenen anhand einer Eingabe in natürlicher
Sprache, über den Textkodierer von CLIP ViT-B-32.

Unmittelbare Grundlage für das Werkzeug `search_scenes_by_prompt` des ReAct-Assistenten
und zugleich für ein freies Suchfeld in der Oberfläche.

Exemples de queries :
    - "wide drone shot at sunset"
    - "person talking in close-up"
    - "fast action with lots of movement"
    - "peaceful landscape without people"

Der Textkodierer wird nur einmal geladen, als träge erzeugte Einzelinstanz, und im Arbeitsspeicher gehalten.
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

# Umformulieren der Anfrage: llama3 reichert die Nutzereingabe vor der CLIP-Einbettung an.
# "animal" → "a cartoon animal character, close-up, expressive face" — CLIP
# arbeitet besser mit Beschreibungen, die viele sichtbare Merkmale nennen.
REWRITE_MODEL = "llama3"
_REWRITE_CACHE: dict[str, str] = {}  # simple mémoire in-process (max ~100 entries en pratique)


async def _rewrite_query(query: str) -> str:
    """Gibt eine angereicherte Fassung der Anfrage für die CLIP-Einbettung zurück.

    Im Fehlerfall, etwa wenn Ollama nicht antwortet oder die Antwort unlesbar ist, kommt die
    ursprüngliche Anfrage zurück; die Suche läuft weiter, nur ungenauer.
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
            # Mit der ursprünglichen Anfrage verbinden, damit die genauen Wörter erhalten bleiben
            # (für BM25 in der Oberfläche nützlich, hier selbst nicht verwendet).
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
    # Nutzt den gemeinsamen Encoder (core/clip_encoder), also dasselbe Modell wie beim Einlesen.
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
    # Gemischt: Gewichtung zwischen Bild (CLIP) und Text (BM25 auf Transkription
    # und beschreibung). Die Summe ist 1.0. Die Voreinstellung 0.65 zu 0.35 bevorzugt das Bild, hält aber
    # den Text für Begriffe wie "Dialog", "Brief" oder Eigennamen wirksam.
    weight_clip: float = Field(0.65, ge=0.0, le=1.0)
    weight_text: float = Field(0.35, ge=0.0, le=1.0)
    # Umformulieren der Anfrage: llama3 reichert sie vor der CLIP-Einbettung an, kostet fünf bis fünfzehn
    # der üblichen Ähnlichkeitswerte. Der erste Aufruf kostet ein bis zwei Sekunden, danach zwischengespeichert.
    rewrite: bool = Field(True, description="Aktiviert LLM-basiertes Query-Rewriting vor CLIP-Embedding.")
    # Reihenfolge: "auto" = exakte Dialog-Treffer (Whisper) ZUERST, danach visuell/kombiniert;
    # "dialog" = nur Transkript-Treffer; "visuell" = altes Verhalten (CLIP+BM25 gemischt).
    modus: str = Field("auto", pattern="^(auto|dialog|visuell)$")


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
    framing: str | None = None  # closeup/medium/wide… für Filter unmittelbar in der Anfrage
    face_count: int | None = None
    # Dialog-Treffer (Whisper, Wort-Zeitstempel): wo genau wird das Wort gesagt?
    treffer_art: str | None = None        # "dialog" | "visuell" | "beides"
    treffer_zeit: float | None = None     # Sekunden im Clip (Start des ersten passenden Worts)
    treffer_wort: str | None = None
    treffer_snippet: str | None = None    # ±6 Wörter Kontext
    treffer_zeiten: list[float] | None = None   # alle Fundstellen (Sekunden) in dieser Szene
    treffer_konfidenz: float | None = None      # Whisper-Wortwahrscheinlichkeit des ersten Treffers (0..1)
    ordner_id: str | None = None


class SearchResponse(BaseModel):
    query: str
    query_rewritten: str | None = None   # angereicherte Fassung, die für die Einbettung dient
    results: list[SearchResult]
    count: int
    scanned: int
    elapsed_ms: float


# ─── Endpoint ────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def _tokenize(text: str | None) -> list[str]:
    """Schlanke mehrsprachige Zerlegung (Deutsch, Englisch, Französisch) für BM25: alphanumerische Wörter
    minuscules, préserve umlauts. Zéro dépendance NLP.
    """
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _norm_wort(w: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]", "", (w or "").lower())


def _dialog_treffer(s: Szene, query_tokens: list[str], phrase: str) -> tuple[list[float], str | None, str | None, float | None]:
    """Sucht die Query-Wörter in den Whisper-Wortzeitstempeln einer Szene.
    Rückgabe (Zeiten aller Fundstellen, erstes Wort, Snippet). Mehrwort-Query: Phrase bevorzugt
    (aufeinanderfolgende Wörter), sonst jedes Wort einzeln. Fällt auf Segment-Start zurück, wenn keine
    Wort-Zeitstempel vorhanden sind."""
    woerter: list[tuple[str, float]] = []   # (normalisiert, start)
    roh: list[str] = []
    probs: list[float] = []
    for seg in (s.transkription_json or []):
        ws = seg.get("woerter") or []
        if ws:
            for w in ws:
                woerter.append((_norm_wort(w.get("wort", "")), float(w.get("start", seg.get("start", 0.0)) or 0.0)))
                roh.append(w.get("wort", ""))
                probs.append(float(w.get("p", 1.0)))
        else:
            for tok in (seg.get("text") or "").split():
                woerter.append((_norm_wort(tok), float(seg.get("start", 0.0) or 0.0)))
                roh.append(tok)
                probs.append(1.0)
    if not woerter:
        return [], None, None, None
    q = [t for t in query_tokens if t]
    if not q:
        return [], None, None, None
    zeiten: list[float] = []
    erster_idx: int | None = None
    # Phrase (alle Tokens hintereinander)
    if len(q) > 1:
        for i in range(len(woerter) - len(q) + 1):
            if all(woerter[i + k][0] == q[k] or woerter[i + k][0].startswith(q[k]) for k in range(len(q))):
                zeiten.append(woerter[i][1])
                if erster_idx is None:
                    erster_idx = i
    if not zeiten:
        for i, (w, t) in enumerate(woerter):
            if any(w == qt or (len(qt) >= 4 and w.startswith(qt)) for qt in q):
                zeiten.append(t)
                if erster_idx is None:
                    erster_idx = i
    if erster_idx is None:
        return [], None, None, None
    a, b = max(0, erster_idx - 6), min(len(roh), erster_idx + 7)
    snippet = ("… " if a > 0 else "") + " ".join(roh[a:b]).strip() + (" …" if b < len(roh) else "")
    return sorted(set(round(z, 2) for z in zeiten)), roh[erster_idx], snippet, probs[erster_idx]


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
    """Findet Szenen, die denen eines gegebenen Clips visuell ähneln.

    Nutzt die bereits bei der Aufnahme berechnete CLIP-Einbettung, ohne Umformulieren
    und ohne Textkodierer. Geeignet für die Frage nach weiteren Einstellungen,
    die dieser hier gleichen, ausgehend von der Zeitleiste.
    """
    t0 = time.time()

    # Lädt die Einbettungen des Ausgangsclips samt Clip-Bezug für die Beschriftung
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

    # Anfragevektor: Mittel der Einbettungen der Ausgangsszenen oder die einer einzelnen Szene
    src_embs = np.array([s.clip_embedding for s in src_scenes], dtype=np.float32)
    if src_embs.shape[0] > 1:
        query_emb = src_embs.mean(axis=0)
    else:
        query_emb = src_embs[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    # Lädt alle übrigen Szenen
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
    Gemischte Suche: CLIP für das Bild, BM25 über beschreibung und transkription für den Text.

    - CLIP text encoder → cosine similarity contre `szene.clip_embedding` (512-D).
    - BM25 über die je Szene vorliegenden Texte, also die Beschreibung von LLaMA3
      und die Whisper-Transkription. Der Wert wird über das Rundenmaximum auf [0..1] normiert.
    - Score final = w_clip * cosine + w_text * bm25_norm.

    Das Kosinusmaß findet "was passiert im brief" nicht, weil die Bildbedeutung zu unscharf ist.
    BM25 holt die Szenen, in deren Transkript "brief" tatsächlich VORKOMMT.
    """
    t0 = time.time()

    from sqlalchemy import or_
    stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(or_(Szene.clip_embedding.isnot(None), Szene.transkription.isnot(None)))
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
    if req.rewrite and req.modus != "dialog":
        expanded = await _rewrite_query(req.query)
        if expanded and expanded != req.query:
            query_for_embed = expanded
            query_rewritten = expanded

    # ── Composante CLIP (visuel) — Szenen ohne Embedding (Analyse läuft noch / reine Audiodatei) = 0 ──
    clip_scores = np.zeros(len(scenes), dtype=np.float32)
    if req.modus != "dialog":
        query_emb = _embed_text(query_for_embed)
        dim = int(query_emb.shape[0])
        emb_idx = [i for i, s in enumerate(scenes) if s.clip_embedding is not None and len(s.clip_embedding) == dim]
        if emb_idx:
            embs = np.array([scenes[i].clip_embedding for i in emb_idx], dtype=np.float32)
            norms = np.linalg.norm(embs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            sims = (embs / norms) @ query_emb
            for j, i in enumerate(emb_idx):
                clip_scores[i] = float(sims[j])

    # ── Composante BM25 (texte : beschreibung + transkription) ──────
    corpus_tokens = [
        _tokenize((s.beschreibung or "") + " " + (s.transkription or ""))
        for s in scenes
    ]
    query_tokens = _tokenize(req.query)
    text_scores = np.zeros(len(scenes), dtype=np.float32)
    if query_tokens and any(corpus_tokens):
        # Szenen ohne jedes Wort aussortieren, BM25Okapi kommt mit leeren Dokumenten nicht zurecht.
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

    # ── Dialog-Treffer (Whisper-Wortzeitstempel) ───────────
    phrase = " ".join(query_tokens)
    dialog: dict[int, tuple[list[float], str | None, str | None, float | None]] = {}
    for i, s in enumerate(scenes):
        if s.transkription:
            z, w, sn, pw = _dialog_treffer(s, query_tokens, phrase)
            if z:
                dialog[i] = (z, w, sn, pw)

    if req.modus == "visuell":
        order = list(np.argsort(combined)[::-1])
    else:
        # Zuerst exakte Dialog-Treffer (nach Textscore, dann Zeit), danach der Rest nach kombiniertem Score.
        d_idx = sorted(dialog.keys(), key=lambda i: (-float(text_scores[i]), scenes[i].clip.dateiname if scenes[i].clip else "", scenes[i].start_zeit))
        # Visuelle Auffüller nur, wenn CLIP wirklich etwas erkennt (≥ 0,22) — sonst wären es Zufallstreffer.
        rest = [int(i) for i in np.argsort(combined)[::-1] if int(i) not in dialog and float(clip_scores[int(i)]) >= 0.22]
        order = d_idx + ([] if req.modus == "dialog" else rest)

    results: list[SearchResult] = []
    for idx in order:
        idx = int(idx)
        score = float(combined[idx])
        ist_dialog = idx in dialog
        if not ist_dialog and (score < req.min_similarity or req.modus == "dialog"):
            if req.modus == "dialog":
                break
            continue
        if len(results) >= req.limit:
            break
        s = scenes[idx]
        z, w, sn, pw = dialog.get(idx, ([], None, None, None))
        results.append(SearchResult(
            treffer_konfidenz=pw,
            treffer_art=("beides" if ist_dialog and float(clip_scores[idx]) >= 0.25 else "dialog" if ist_dialog else "visuell"),
            treffer_zeit=(z[0] if z else None),
            treffer_wort=w,
            treffer_snippet=sn,
            treffer_zeiten=(z or None),
            ordner_id=(str(s.clip.ordner_id) if s.clip and s.clip.ordner_id else None),
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
            similarity=(max(score, 0.5 + 0.5 * float(text_scores[idx])) if ist_dialog else score),
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
