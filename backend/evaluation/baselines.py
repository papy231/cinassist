"""Baselines pour la comparaison quantitative du générateur timeline-from-prompt.

Chaque baseline produit un triplet (plan, candidates, timeline) STRUCTURELLEMENT
compatible avec ceux du pipeline principal (`backend/core/timeline_generator.py`)
afin que `backend/evaluation/metrics.compute_all` calcule les mêmes métriques.

Baselines implémentées
──────────────────────
- **random_pick** : ignore complètement le prompt, tire des scènes au hasard
  jusqu'à couvrir la durée cible. Baseline « floor » qui montre ce qu'apporte
  déjà le simple respect de la durée.
- **top1_no_filter** : plan LLM identique au pipeline principal, mais retrieve
  SANS filtres durs (framing/speaker/dialogue) — on prend juste le meilleur
  score CLIP+BM25 par slot, sans dédup. Baseline « pré-filtres » qui isole
  la contribution des filtres durs et de la déduplication.
- **single_shot_llm_direct** : un seul call qwen qui reçoit le pool compact
  et le prompt, et produit directement les picks (pas de Plan → Retrieve →
  Assemble). Baseline « pré-décomposition » qui montre ce qu'apporte
  l'architecture en 3 phases.

Toutes les baselines retournent le même dict-triplet :
    {"plan": {...}, "candidates": {...}, "timeline": {...},
     "pool_summary_wall_s": float}
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import AsyncSessionLocal, Szene
from backend.core.timeline_generator import (
    AGENT_MODEL,
    DEFAULT_TIMEOUT_S,
    OLLAMA_URL,
    _fetch_pool_scenes,
    _fetch_scene_speaker_map,
    _scene_to_candidate,
    plan_timeline,
    retrieve_candidates,
    smart_trim_start,
    summarize_pool,
)


# ─── Helpers communs ─────────────────────────────────────────

def _empty_plan_slot(slot_id: int, duration_s: float,
                     intent_de: str = "", intent_en: str = "",
                     framing_hint: str = "any") -> dict:
    """Slot dummy pour les baselines qui n'ont pas de vrai plan LLM."""
    return {
        "slot_id": slot_id,
        "intent_de": intent_de,
        "intent_en": intent_en,
        "duration_min_s": max(1.0, duration_s * 0.8),
        "duration_max_s": duration_s * 1.2,
        "framing_hint": framing_hint,
        "needs_speaker": False,
        "needs_dialogue": False,
        "notes_de": "baseline dummy slot",
    }


def _segment_from_pick(pick: dict, slot: dict, target_duration: float,
                        trim_start: float, trim_strategy: str) -> tuple[dict, dict]:
    """Fabrique (segment, decision) au format attendu par les métriques."""
    media_start = float(pick.get("start_zeit") or 0.0) + trim_start
    segment = {
        "clip_path": pick["clip_path"],
        "clip_name": pick["clip_name"],
        "media_start": round(media_start, 3),
        "duration": round(target_duration, 3),
        "src_scene_id": pick["scene_id"],
    }
    decision = {
        "slot_id": str(slot["slot_id"]),
        "intent_de": slot.get("intent_de"),
        "outcome": "picked",
        "scene_id": pick["scene_id"],
        "clip_name": pick["clip_name"],
        "clip_score": pick.get("clip_score", 0.0),
        "text_score": pick.get("text_score", 0.0),
        "score": pick.get("score", pick.get("clip_score", 0.0)),
        "target_duration_s": round(target_duration, 3),
        "trim_start_in_scene_s": round(trim_start, 3),
        "trim_strategy": trim_strategy,
        "framing": pick.get("framing"),
        "runner_ups": [],
    }
    return segment, decision


# ─── Baseline 1 : random_pick ────────────────────────────────

async def run_baseline_random_pick(user_prompt: str, duration_s: float,
                                   clip_ids: list[str], seed: int = 42) -> dict:
    """Baseline « floor » : ignore le prompt, tire au hasard jusqu'à couvrir
    la durée cible. Utilise seulement les scènes avec `clip_embedding` (comme
    le pipeline principal) pour être comparable.

    Aucun filtre framing/speaker/dialogue, aucun scoring — pur random.
    """
    t_pool = time.time()
    async with AsyncSessionLocal() as db:
        pool_summary = await summarize_pool(db, clip_ids)
        pool = await _fetch_pool_scenes(db, clip_ids)
    pool_wall = time.time() - t_pool

    t0 = time.time()
    rng = random.Random(seed)
    shuffled = list(pool)
    rng.shuffle(shuffled)

    plan_slots: list[dict] = []
    candidates_slots: dict[str, list[dict]] = {}
    segments: list[dict] = []
    decisions: list[dict] = []

    accumulated = 0.0
    slot_id = 0
    for scene in shuffled:
        if accumulated >= duration_s:
            break
        slot_id += 1
        scene_dauer = float(scene.dauer or 0.0)
        if scene_dauer <= 0:
            continue
        remaining = duration_s - accumulated
        # Cible : min(scene_dauer, remaining, 8s) mais au moins 2s
        target = max(2.0, min(scene_dauer, remaining, 8.0))
        slot = _empty_plan_slot(slot_id, target)
        plan_slots.append(slot)

        candidate = _scene_to_candidate(0.0, 0.0, 0.0, scene)
        candidates_slots[str(slot_id)] = [candidate]

        # Trim centré (baseline ne fait pas de smart_trim)
        if scene_dauer > target:
            trim_start = (scene_dauer - target) / 2.0
        else:
            trim_start = 0.0
            target = scene_dauer

        segment, decision = _segment_from_pick(candidate, slot, target,
                                                trim_start, "random_centered")
        segments.append(segment)
        decisions.append(decision)
        accumulated += target

    wall = time.time() - t0

    plan = {
        "narrative_intent_de": "(baseline random — kein Prompt-Verständnis)",
        "target_duration_s": duration_s,
        "planned_total_duration_s": round(accumulated, 2),
        "slots": plan_slots,
        "_meta": {
            "wall_s": 0.0,
            "model": "baseline_random",
            "temperature": 0.0,
            "user_prompt": user_prompt,
            "seed": seed,
        },
    }
    candidates = {
        "slots": candidates_slots,
        "_meta": {
            "wall_s": 0.0,
            "pool_size": len(pool),
            "pool_with_text": 0,
            "top_k": 1,
            "dedupe_across_slots": True,
            "baseline": "random_pick",
        },
    }
    timeline = {
        "segments": segments,
        "decisions": decisions,
        "_meta": {
            "wall_s": round(wall, 2),
            "mode": "baseline_random",
            "segment_count": len(segments),
            "total_duration_s": round(accumulated, 2),
            "skipped_slots": 0,
            "llm": {},
        },
    }
    return {"plan": plan, "candidates": candidates, "timeline": timeline,
            "pool_summary": pool_summary, "pool_summary_wall_s": pool_wall}


# ─── Baseline 2 : top1_no_filter ─────────────────────────────

async def run_baseline_top1_no_filter(user_prompt: str, duration_s: float,
                                       clip_ids: list[str],
                                       num_slots_hint: int | None = None) -> dict:
    """Plan LLM identique (avec pool_summary), retrieve SANS filtres durs et
    SANS dédup — pur top-1 hybride CLIP+BM25 par slot. Ensuite assemble
    heuristic (top-1 + trim centré).

    Isole la contribution des filtres durs framing/speaker/dialogue + du dédup.
    """
    t_pool = time.time()
    async with AsyncSessionLocal() as db:
        pool_summary = await summarize_pool(db, clip_ids)
    pool_wall = time.time() - t_pool

    # Phase 1 : plan normal
    plan = await plan_timeline(user_prompt, duration_s, num_slots_hint,
                               pool_summary=pool_summary)

    # Phase 2 : retrieve custom (contourne les filtres durs)
    async with AsyncSessionLocal() as db:
        candidates = await _retrieve_no_filter(plan, clip_ids, db, top_k=1)

    # Phase 3 : assemble heuristic manuel (top-1 + trim centré, sans smart_trim
    # sophistiqué — pour vraiment isoler la contribution de smart_trim, on
    # utilise le fallback centré uniquement).
    t_asm = time.time()
    slots_c = candidates.get("slots") or {}
    segments: list[dict] = []
    decisions: list[dict] = []
    for slot in plan.get("slots") or []:
        sid = str(slot["slot_id"])
        picks = slots_c.get(sid, [])
        if not picks:
            decisions.append({
                "slot_id": sid, "intent_de": slot.get("intent_de"),
                "outcome": "skipped_no_candidate",
            })
            continue
        pick = picks[0]
        scene_dauer = float(pick.get("dauer") or 0.0)
        dmin = float(slot.get("duration_min_s", 2.0))
        dmax = float(slot.get("duration_max_s", dmin))
        target = min(dmax, scene_dauer) if scene_dauer > dmin else max(1.0, scene_dauer)
        trim_start = max(0.0, (scene_dauer - target) / 2.0) if scene_dauer > target else 0.0
        segment, decision = _segment_from_pick(pick, slot, target, trim_start,
                                                "centered_baseline")
        segments.append(segment)
        decisions.append(decision)
    asm_wall = time.time() - t_asm

    total_dur = sum(float(s["duration"]) for s in segments)
    timeline = {
        "segments": segments,
        "decisions": decisions,
        "_meta": {
            "wall_s": round(asm_wall, 2),
            "mode": "baseline_no_filter",
            "segment_count": len(segments),
            "total_duration_s": round(total_dur, 2),
            "skipped_slots": sum(1 for d in decisions
                                  if d.get("outcome") == "skipped_no_candidate"),
            "llm": {},
        },
    }
    return {"plan": plan, "candidates": candidates, "timeline": timeline,
            "pool_summary": pool_summary, "pool_summary_wall_s": pool_wall}


async def _retrieve_no_filter(plan: dict, project_clip_ids: list[str],
                               db: AsyncSession, top_k: int = 1) -> dict:
    """Copie de retrieve_candidates SANS filtres durs et SANS dédup. Utilise
    quand même le scoring hybride CLIP+BM25 pour rester comparable.
    """
    import numpy as np
    from rank_bm25 import BM25Okapi
    from backend.core.timeline_generator import (
        _cosine, _embed_text_lazy, _tokenize_lazy,
    )

    t0 = time.time()
    slots = plan.get("slots") or []
    if not slots or not project_clip_ids:
        return {"slots": {}, "_meta": {"wall_s": 0.0, "pool_size": 0,
                                        "baseline": "no_filter"}}

    pool = await _fetch_pool_scenes(db, project_clip_ids)
    corpus_tokens = [
        _tokenize_lazy((s.beschreibung or "") + " " + (s.transkription or ""))
        for s in pool
    ]
    non_empty_idx = [i for i, toks in enumerate(corpus_tokens) if toks]
    bm25 = BM25Okapi([corpus_tokens[i] for i in non_empty_idx]) if non_empty_idx else None

    w_clip, w_text = 0.6, 0.4
    result: dict[str, list[dict]] = {}

    for slot in slots:
        sid = str(slot.get("slot_id"))
        intent_en = slot.get("intent_en") or slot.get("intent_de") or ""
        try:
            query_emb = _embed_text_lazy(intent_en)
        except Exception:
            result[sid] = []
            continue

        query_tokens = _tokenize_lazy(
            (slot.get("intent_de") or "") + " " + (slot.get("intent_en") or "")
        )
        text_scores_pool = np.zeros(len(pool), dtype=np.float32)
        if bm25 is not None and query_tokens:
            raw = bm25.get_scores(query_tokens)
            max_raw = float(raw.max()) if raw.size > 0 else 0.0
            if max_raw > 0:
                for j, idx in enumerate(non_empty_idx):
                    text_scores_pool[idx] = float(raw[j]) / max_raw

        scored = []
        for i, sc in enumerate(pool):
            try:
                sc_emb = np.asarray(sc.clip_embedding, dtype=np.float32)
                clip_score = _cosine(query_emb, sc_emb)
            except Exception:
                continue
            text_score = float(text_scores_pool[i])
            combined = w_clip * clip_score + w_text * text_score
            scored.append((combined, clip_score, text_score, sc))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]
        result[sid] = [_scene_to_candidate(c, cs, ts, sc) for c, cs, ts, sc in top]

    return {
        "slots": result,
        "_meta": {
            "wall_s": round(time.time() - t0, 2),
            "pool_size": len(pool),
            "pool_with_text": len(non_empty_idx),
            "top_k": top_k,
            "dedupe_across_slots": False,
            "baseline": "no_filter",
        },
    }


# ─── Baseline 3 : single_shot_llm_direct ─────────────────────

SINGLE_SHOT_SYSTEM_PROMPT = """Du bist ein Cutting-Assistent. Du bekommst einen
Prompt des Benutzers, eine Zieldauer, und eine nummerierte Liste verfügbarer
Szenen aus dem Projekt-Pool (mit Index, Framing, Dauer und kurzer Beschreibung).

Deine Aufgabe: Wähle direkt eine geordnete Liste von Szenen aus, die den
Prompt in der Zieldauer umsetzen. Angabe pro Pick: scene_idx (die Ziffer aus
der Liste), target_duration_s, trim_start_s (Position innerhalb der Szene),
und optional rationale_de.

REGELN:
- Die Summe aller target_duration_s soll ungefähr der Zieldauer entsprechen (±15%).
- target_duration_s MUSS ≤ Dauer der gewählten Szene sein.
- trim_start_s MUSS ≥ 0 und ≤ (scene.dauer - target_duration_s) sein.
- scene_idx MUSS eine Ziffer aus der Liste sein (0 bis N-1).
- Vermeide, dieselbe Szene zweimal hintereinander zu wählen.
- Wähle 3-15 Szenen (je nach Zieldauer).

Antworte AUSSCHLIESSLICH mit gültigem JSON:

{
  "narrative_intent_de": "kurzer Satz zum Cut-Bogen",
  "picks": [
    {
      "scene_idx": 12,
      "target_duration_s": 5.0,
      "trim_start_s": 0.5,
      "rationale_de": "kurzer Grund"
    }
  ]
}
"""


def _compact_pool_for_single_shot(pool: list[Szene],
                                    max_scenes: int = 80) -> tuple[str, list[Szene]]:
    """Rendu texte compact du pool pour injection dans le prompt LLM.

    Retourne (text, ordered_pool) : ordered_pool est la liste des scènes dans
    l'ordre affiché — les picks du LLM référencent leur index dans cette liste.
    """
    lines: list[str] = []
    sorted_pool = sorted(
        pool,
        key=lambda s: (
            0 if s.beschreibung else 1,
            0 if s.transkription else 1,
            -(s.dauer or 0.0),
        ),
    )[:max_scenes]
    for idx, sc in enumerate(sorted_pool):
        desc = (sc.beschreibung or "").strip().replace("\n", " ")[:110]
        trans = (sc.transkription or "").strip().replace("\n", " ")[:60]
        parts = [
            f"[{idx}]",
            f"framing={sc.framing or 'unknown'}",
            f"dauer={float(sc.dauer or 0.0):.1f}s",
        ]
        if desc:
            parts.append(f'beschr="{desc}"')
        if trans:
            parts.append(f'trans="{trans}"')
        lines.append(" ".join(parts))
    return "\n".join(lines), sorted_pool


async def run_baseline_single_shot_llm(user_prompt: str, duration_s: float,
                                        clip_ids: list[str],
                                        max_scenes_in_prompt: int = 80,
                                        temperature: float = 0.3) -> dict:
    """Un seul call qwen : pool compact + prompt → picks directs. Pas de Plan,
    pas de Retrieve, pas de contraintes framing/speaker/dialogue.
    """
    t_pool = time.time()
    async with AsyncSessionLocal() as db:
        pool_summary = await summarize_pool(db, clip_ids)
        pool = await _fetch_pool_scenes(db, clip_ids)
    pool_wall = time.time() - t_pool

    pool_str, ordered_pool = _compact_pool_for_single_shot(pool, max_scenes_in_prompt)

    prompt = (
        f"{SINGLE_SHOT_SYSTEM_PROMPT}\n\n"
        f"=== VERFÜGBARE SZENEN ({len(ordered_pool)} von {len(pool)}, Index 0 bis {len(ordered_pool)-1}) ===\n"
        f"{pool_str}\n\n"
        f"=== AUFGABE ===\n"
        f"Zieldauer: {duration_s:.0f} Sekunden.\n"
        f"Benutzer-Prompt: {user_prompt.strip()}\n\n"
        "Generiere jetzt das JSON mit picks."
    )

    t_llm = time.time()
    parsed: dict = {}
    llm_wall = 0.0
    error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
            r = await client.post(
                OLLAMA_URL,
                json={
                    "model": AGENT_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": temperature},
                },
            )
            r.raise_for_status()
            data = r.json()
        parsed = json.loads(data.get("response", "{}"))
        llm_wall = time.time() - t_llm
    except Exception as e:
        error = str(e)
        llm_wall = time.time() - t_llm

    # Reconstruit plan / candidates / timeline compatibles avec compute_all
    plan_slots: list[dict] = []
    candidates_slots: dict[str, list[dict]] = {}
    segments: list[dict] = []
    decisions: list[dict] = []

    picks = parsed.get("picks") if isinstance(parsed, dict) else None
    picks = picks if isinstance(picks, list) else []

    for i, p in enumerate(picks, 1):
        if not isinstance(p, dict):
            continue
        # Accepte scene_idx (nouveau, index dans ordered_pool) et scene_id (legacy, UUID)
        scene = None
        raw_idx = p.get("scene_idx")
        if raw_idx is not None:
            try:
                idx = int(raw_idx)
                if 0 <= idx < len(ordered_pool):
                    scene = ordered_pool[idx]
            except (TypeError, ValueError):
                pass
        if scene is None and p.get("scene_id"):
            pool_by_id = {str(s.id): s for s in pool}
            scene = pool_by_id.get(str(p.get("scene_id")))
        if scene is None:
            decisions.append({
                "slot_id": str(i), "intent_de": p.get("rationale_de"),
                "outcome": "skipped_invalid_scene_id",
            })
            plan_slots.append(_empty_plan_slot(i, 5.0))
            continue

        scene_dauer = float(scene.dauer or 0.0)
        try:
            target = float(p.get("target_duration_s") or 5.0)
        except (TypeError, ValueError):
            target = 5.0
        target = max(1.0, min(target, scene_dauer))
        try:
            trim_start = float(p.get("trim_start_s") or 0.0)
        except (TypeError, ValueError):
            trim_start = 0.0
        trim_start = max(0.0, min(trim_start, max(0.0, scene_dauer - target)))

        slot = _empty_plan_slot(
            i, target, framing_hint=scene.framing or "any",
            intent_de=p.get("rationale_de") or "",
        )
        plan_slots.append(slot)

        candidate = _scene_to_candidate(0.0, 0.0, 0.0, scene)
        candidates_slots[str(i)] = [candidate]

        segment, decision = _segment_from_pick(candidate, slot, target,
                                                trim_start, "llm_provided")
        segments.append(segment)
        decisions.append(decision)

    total_dur = sum(float(s["duration"]) for s in segments)

    plan = {
        "narrative_intent_de": (parsed or {}).get("narrative_intent_de", ""),
        "target_duration_s": duration_s,
        "planned_total_duration_s": round(total_dur, 2),
        "slots": plan_slots,
        "_meta": {
            "wall_s": round(llm_wall, 2),
            "model": AGENT_MODEL,
            "temperature": temperature,
            "user_prompt": user_prompt,
            "baseline": "single_shot_llm",
            "error": error,
        },
    }
    candidates = {
        "slots": candidates_slots,
        "_meta": {
            "wall_s": 0.0,
            "pool_size": len(pool),
            "pool_scenes_in_prompt": min(len(pool), max_scenes_in_prompt),
            "top_k": 1,
            "dedupe_across_slots": False,
            "baseline": "single_shot_llm",
        },
    }
    timeline = {
        "segments": segments,
        "decisions": decisions,
        "_meta": {
            "wall_s": round(llm_wall, 2),
            "mode": "baseline_single_shot",
            "segment_count": len(segments),
            "total_duration_s": round(total_dur, 2),
            "skipped_slots": sum(1 for d in decisions
                                  if d.get("outcome") != "picked"),
            "llm": {"wall_s": round(llm_wall, 2), "error": error},
        },
    }

    return {"plan": plan, "candidates": candidates, "timeline": timeline,
            "pool_summary": pool_summary, "pool_summary_wall_s": pool_wall}


# ─── Dispatcher ──────────────────────────────────────────────

BASELINE_MODES = {
    "baseline_random",
    "baseline_no_filter",
    "baseline_single_shot",
}


async def run_baseline(mode: str, user_prompt: str, duration_s: float,
                       clip_ids: list[str]) -> dict:
    """Dispatcher pour lancer une baseline depuis run_benchmark.

    Retourne un triplet {plan, candidates, timeline, pool_summary,
    pool_summary_wall_s} compatible avec `M.compute_all`.
    """
    if mode == "baseline_random":
        return await run_baseline_random_pick(user_prompt, duration_s, clip_ids)
    if mode == "baseline_no_filter":
        return await run_baseline_top1_no_filter(user_prompt, duration_s, clip_ids)
    if mode == "baseline_single_shot":
        return await run_baseline_single_shot_llm(user_prompt, duration_s, clip_ids)
    raise ValueError(f"Unknown baseline mode: {mode!r}")
