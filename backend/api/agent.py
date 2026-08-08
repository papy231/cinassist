"""
CinAssist — Agent ReAct (Vague 1.4)

Endpoint : POST /api/agent/run — streaming SSE d'un agent conversationnel
qui décompose une intention en langage naturel en une chaîne de tool calls.

Modèle : qwen2.5:14b en local via Ollama (format=json pour parsing fiable).

Architecture :
    - Loop ReAct classique : thought → action → observation → loop
    - Chaque tool = coroutine async(args, db) -> dict
    - Streaming SSE : chaque étape (thought/action/observation/done) est
      poussée au frontend en temps réel

Ce MVP expose 4 tools pour valider l'orchestration ; on ajoutera face
detection / diarization / assemblage / export au fur et à mesure des
Vagues 1.2 → 1.6.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.search import _embed_text
from backend.core.config import OLLAMA_BASE_URL
from backend.core.database import Clip, SceneSpeaker, Speaker, Szene, get_db
from backend.core.otio_export import export_to_file as _otio_export_to_file

logger = logging.getLogger("cinassist.agent")
router = APIRouter(prefix="/api/agent", tags=["Agent"])

# ─── Config ──────────────────────────────────────────────────
AGENT_MODEL = "qwen2.5:14b"
MAX_ITERATIONS = 12
TEMPERATURE = 0.2


# ─── Tool infrastructure ─────────────────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict[str, str]  # arg_name -> "type — description"
    handler: Callable[..., Awaitable[dict]]


async def _tool_list_clips(args: dict, db: AsyncSession) -> dict:
    """Liste tous les clips uploadés avec leurs métadonnées de base."""
    result = await db.execute(
        select(Clip).options(selectinload(Clip.szenen))
    )
    clips = result.scalars().all()
    return {
        "clips": [
            {
                "clip_id": str(c.id),
                "dateiname": c.dateiname,
                "dauer_s": c.dauer,
                "status": c.status,
                "scene_count": len(c.szenen),
            }
            for c in clips
        ]
    }


async def _tool_search_scenes_by_prompt(args: dict, db: AsyncSession) -> dict:
    """CLIP text search sur toutes les scènes avec embedding."""
    query = args.get("query", "")
    limit = int(args.get("limit", 5))
    if not query:
        return {"error": "query is required"}

    query_emb = _embed_text(query)

    import numpy as np

    stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(Szene.clip_embedding.isnot(None))
    )
    result = await db.execute(stmt)
    scenes = result.scalars().all()
    if not scenes:
        return {"results": [], "message": "no scenes with embeddings in database"}

    embs = np.array([s.clip_embedding for s in scenes], dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    sims = (embs / norms) @ query_emb

    order = np.argsort(sims)[::-1][:limit]
    return {
        "results": [
            {
                "scene_id": str(scenes[i].id),
                "clip_name": scenes[i].clip.dateiname if scenes[i].clip else "",
                "start": round(float(scenes[i].start_zeit), 2),
                "duration": round(float(scenes[i].dauer), 2),
                "description": scenes[i].beschreibung,
                "similarity": round(float(sims[i]), 3),
            }
            for i in order
        ]
    }


async def _tool_list_speakers(args: dict, db: AsyncSession) -> dict:
    """Liste tous les speakers identifiés dans le projet (label auto + rename manuel)."""
    from sqlalchemy import func
    stmt = select(Speaker, Clip.dateiname).join(Clip, Clip.id == Speaker.clip_id).order_by(Speaker.total_speaking_time.desc())
    result = await db.execute(stmt)
    rows = result.all()
    return {
        "count": len(rows),
        "speakers": [
            {
                "speaker_id": str(sp.id),
                "clip_name": name,
                "label_auto": sp.label_auto,
                "label_manual": sp.label_manual,
                "display_name": sp.label_manual or sp.label_auto,
                "total_speaking_time_s": round(sp.total_speaking_time or 0.0, 2),
                "segment_count": sp.segment_count or 0,
            }
            for sp, name in rows
        ],
    }


async def _tool_filter_by_speaker(args: dict, db: AsyncSession) -> dict:
    """Retourne les scènes où un speaker donné (par label_manual ou label_auto) apparaît."""
    name = (args.get("speaker") or "").strip()
    if not name:
        return {"error": "speaker (name) is required"}
    limit = int(args.get("limit") or 20)

    # Résoudre le speaker par label_manual OU label_auto (case-insensitive)
    from sqlalchemy import or_, func
    sp_stmt = select(Speaker).where(
        or_(
            func.lower(Speaker.label_manual) == name.lower(),
            func.lower(Speaker.label_auto) == name.lower(),
        )
    )
    sp_result = await db.execute(sp_stmt)
    speakers = sp_result.scalars().all()
    if not speakers:
        # Suggestion : liste des noms dispos
        all_stmt = select(Speaker.label_manual, Speaker.label_auto)
        all_result = await db.execute(all_stmt)
        available = [(m or a) for m, a in all_result.all()]
        return {
            "error": f"speaker '{name}' not found",
            "available": available[:20],
        }

    speaker_ids = [sp.id for sp in speakers]
    ss_stmt = (
        select(SceneSpeaker, Szene, Clip.dateiname)
        .join(Szene, Szene.id == SceneSpeaker.scene_id)
        .join(Clip, Clip.id == Szene.clip_id)
        .where(SceneSpeaker.speaker_id.in_(speaker_ids))
        .order_by(Szene.szenen_nr)
        .limit(limit)
    )
    ss_result = await db.execute(ss_stmt)
    rows = ss_result.all()
    return {
        "speaker": name,
        "count": len(rows),
        "scenes": [
            {
                "scene_id": str(sz.id),
                "clip_name": clip_name,
                "start": round(float(sz.start_zeit), 2),
                "duration": round(float(sz.dauer), 2),
                "speaking_time_in_scene_s": round(ss.speaking_time or 0.0, 2),
                "description": (sz.beschreibung or "")[:100],
            }
            for ss, sz, clip_name in rows
        ],
    }


async def _tool_rename_speaker(args: dict, db: AsyncSession) -> dict:
    """Renomme un speaker : label_auto (SPEAKER_00) → label_manual ('Anna').

    Accepte soit un UUID, soit un label_auto ("SPEAKER_00") comme identifiant.
    """
    from sqlalchemy import func, or_

    raw_id = (args.get("speaker_id") or args.get("speaker") or "").strip()
    new_name = (args.get("new_name") or args.get("name") or "").strip()
    if not raw_id or not new_name:
        return {"error": "speaker_id (UUID or label like SPEAKER_00) and new_name are required"}

    if _UUID_RE.match(raw_id):
        result = await db.execute(select(Speaker).where(Speaker.id == raw_id))
    else:
        # Résoudre par label_auto ou label_manual (case-insensitive)
        result = await db.execute(select(Speaker).where(or_(
            func.lower(Speaker.label_auto) == raw_id.lower(),
            func.lower(Speaker.label_manual) == raw_id.lower(),
        )))
    speakers = result.scalars().all()
    if not speakers:
        return {"error": f"speaker '{raw_id}' not found (neither UUID nor label match)"}
    if len(speakers) > 1:
        # Ambigu — plusieurs clips ont un SPEAKER_00 : renomme TOUS (comportement voulu ?)
        # Pour ne pas surprendre, on remonte les candidats et on refuse.
        return {
            "error": f"ambiguous speaker '{raw_id}': matches {len(speakers)} rows across clips",
            "candidates": [
                {"speaker_id": str(sp.id), "clip_id": str(sp.clip_id), "label_auto": sp.label_auto}
                for sp in speakers
            ],
            "_hint": "Pass a UUID from list_speakers to disambiguate.",
        }

    sp = speakers[0]
    old = sp.label_manual
    sp.label_manual = new_name
    await db.commit()
    return {
        "speaker_id": str(sp.id),
        "label_auto": sp.label_auto,
        "old_name": old,
        "new_name": new_name,
    }


async def _tool_filter_by_framing(args: dict, db: AsyncSession) -> dict:
    """Filtre les scènes par framing (extreme_closeup, closeup, medium, wide_with_person, wide_no_person). Accepte str ou list[str]."""
    framing_arg = args.get("framing") or ""
    min_faces = int(args.get("min_faces") or 0)
    limit = int(args.get("limit") or 20)
    valid = {"extreme_closeup", "closeup", "medium", "wide_with_person", "wide_no_person"}

    # Accepte string unique, list, ou string CSV ("closeup,medium")
    if isinstance(framing_arg, list):
        framings = [str(f).strip() for f in framing_arg if str(f).strip()]
    elif isinstance(framing_arg, str):
        framings = [f.strip() for f in framing_arg.split(",") if f.strip()]
    else:
        framings = []

    invalid = [f for f in framings if f not in valid]
    if invalid:
        return {"error": f"invalid framing values: {invalid}. Must be from {sorted(valid)}"}

    stmt = select(Szene).options(selectinload(Szene.clip))
    if framings:
        stmt = stmt.where(Szene.framing.in_(framings))
    if min_faces > 0:
        stmt = stmt.where(Szene.face_count >= min_faces)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    scenes = result.scalars().all()
    return {
        "framing_filter": framings or ["(any)"],
        "min_faces_filter": min_faces,
        "count": len(scenes),
        "scenes": [
            {
                "scene_id": str(s.id),
                "clip_name": s.clip.dateiname if s.clip else "",
                "start": round(float(s.start_zeit), 2),
                "duration": round(float(s.dauer), 2),
                "face_count": s.face_count or 0,
                "framing": s.framing or "unknown",
                "description": (s.beschreibung or "")[:100],
            }
            for s in scenes
        ],
    }


async def _tool_export_scenes(args: dict, db: AsyncSession) -> dict:
    """Exporte une sélection de scènes OU des segments custom en FCPXML/OTIO.

    Le fichier est écrit dans ~/Documents/CinAssist_Exports/ et peut être
    importé dans Premiere Pro, Final Cut Pro X, DaVinci Resolve.

    Deux modes :
      - scene_ids : exporte les scènes complètes de la DB (ordre respecté)
      - segments  : exporte des segments custom (produits par remove_silences,
                    generate_story, generate_timeline_from_prompt, etc.)
    """
    scene_ids = args.get("scene_ids") or []
    segments_arg = args.get("segments") or []
    fmt = args.get("format", "fcpxml").lower()
    name = args.get("name", "CinAssist_Timeline")

    if not scene_ids and not segments_arg:
        return {"error": "either scene_ids (list) or segments (list) is required"}
    if fmt not in ("fcpxml", "otio"):
        return {"error": f"format must be 'fcpxml' or 'otio', got '{fmt}'"}

    if segments_arg:
        # Mode segments : formats attendu {clip_path, clip_name, media_start, duration}
        segments = []
        for s in segments_arg:
            if not isinstance(s, dict):
                continue
            if not s.get("clip_path"):
                continue
            segments.append({
                "clip_path": s["clip_path"],
                "clip_name": s.get("clip_name", "clip"),
                "media_start": float(s.get("media_start", 0)),
                "duration": float(s.get("duration", 0)),
                "track": s.get("track", "v1"),
            })
    else:
        result = await db.execute(
            select(Szene).options(selectinload(Szene.clip)).where(Szene.id.in_(scene_ids))
        )
        scenes = result.scalars().all()
        if not scenes:
            return {"error": f"no scenes found for ids {scene_ids}"}
        by_id = {str(s.id): s for s in scenes}
        ordered = [by_id[sid] for sid in scene_ids if sid in by_id]
        segments = []
        for s in ordered:
            if not s.clip or not s.clip.dateipfad:
                continue
            segments.append({
                "clip_path": s.clip.dateipfad,
                "clip_name": s.clip.dateiname,
                "media_start": float(s.start_zeit),
                "duration": float(s.dauer),
                "track": "v1",
            })

    if not segments:
        return {"error": "no valid segments (missing clip paths)"}

    try:
        info = _otio_export_to_file(segments, format=fmt, name=name)
    except Exception as e:
        logger.exception("OTIO export failed")
        return {"error": f"Export failed: {e}"}
    return info


import re
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


async def _resolve_clip_ids(db: AsyncSession, ids_or_names) -> list[str]:
    """Résout une liste mixte d'UUIDs et de noms de fichiers en UUIDs uniquement.

    - Si l'entrée est un UUID valide → passe tel quel.
    - Sinon → recherche par `dateiname ILIKE %name%` en DB.
    """
    if isinstance(ids_or_names, str):
        ids_or_names = [ids_or_names]
    if not ids_or_names:
        return []
    out: list[str] = []
    for x in ids_or_names:
        s = str(x).strip()
        if not s:
            continue
        if _UUID_RE.match(s):
            out.append(s)
            continue
        # Recherche par nom
        r = await db.execute(
            select(Clip.id).where(Clip.dateiname.ilike(f"%{s}%"))
        )
        matches = [str(row[0]) for row in r.all()]
        out.extend(matches)
    return list(dict.fromkeys(out))  # dedup preserving order


async def _fetch_scenes_for_cleanup(db: AsyncSession, scene_ids: list[str] | None, clip_ids: list[str] | None) -> list[dict]:
    """Fetch scenes as dict for cleanup helpers (attaches clip info)."""
    stmt = select(Szene).options(selectinload(Szene.clip))
    if scene_ids:
        stmt = stmt.where(Szene.id.in_(scene_ids))
    elif clip_ids:
        stmt = stmt.where(Szene.clip_id.in_(clip_ids))
    result = await db.execute(stmt.order_by(Szene.clip_id, Szene.szenen_nr))
    scenes = result.scalars().all()
    out = []
    for s in scenes:
        out.append({
            "id": str(s.id),
            "start_zeit": float(s.start_zeit),
            "end_zeit": float(s.end_zeit),
            "transkription_json": s.transkription_json,
            "clip": {
                "dateipfad": s.clip.dateipfad if s.clip else None,
                "dateiname": s.clip.dateiname if s.clip else "",
            },
        })
    return out


async def _tool_remove_silences(args: dict, db: AsyncSession) -> dict:
    """Entfernt lange Stillen und gibt exportbereite Segmente zurück."""
    from backend.core.cleanup import remove_silences_from_scenes

    scene_ids = args.get("scene_ids")
    clip_ids = args.get("clip_ids")
    min_silence_ms = int(args.get("min_silence_ms") or 800)
    keep_margin_ms = int(args.get("keep_margin_ms") or 150)

    if not scene_ids and not clip_ids:
        return {"error": "either scene_ids or clip_ids is required"}

    # Résoudre noms de clips → UUIDs
    if clip_ids:
        clip_ids = await _resolve_clip_ids(db, clip_ids)
        if not clip_ids:
            return {"error": "no matching clips found for provided names/ids"}

    scenes = await _fetch_scenes_for_cleanup(db, scene_ids, clip_ids)
    if not scenes:
        return {"error": "no scenes found"}

    result = remove_silences_from_scenes(
        scenes, min_silence_ms=min_silence_ms, keep_margin_ms=keep_margin_ms
    )
    # On garde `segments` complet dans le return (le frontend proxy en a besoin
    # pour construire les deleteRange précis). Le prompt LLM tronque de toute
    # façon les observations à 2000 chars → pas de pollution du context.
    out = result.copy()
    out["segment_count"] = len(out["segments"])
    out["segments_preview"] = out["segments"][:5]
    # Attach a stash key so export_scenes can pull the full segments back
    await _stash_write(args.get("_stash_id", "last"), result["segments"], db)
    out["stash_id"] = "last"
    out["_hint"] = "Call export_scenes with {segments: <segments from previous obs>} to export."
    return out


async def _tool_find_hesitations(args: dict, db: AsyncSession) -> dict:
    """Erkennt 'ähm', 'euh', 'um', Wiederholungen in den Transkripten."""
    from backend.core.cleanup import find_hesitations_in_scenes

    scene_ids = args.get("scene_ids")
    clip_ids = args.get("clip_ids")
    if not scene_ids and not clip_ids:
        # Défaut : tous les clips analysés
        r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
        clip_ids = [str(c) for (c,) in r.all()]
        if not clip_ids:
            return {"error": "no analyzed clips"}
    elif clip_ids:
        clip_ids = await _resolve_clip_ids(db, clip_ids)
    scenes = await _fetch_scenes_for_cleanup(db, scene_ids, clip_ids)
    if not scenes:
        return {"error": "no scenes found"}
    return find_hesitations_in_scenes(scenes)


# ─── Stash für Segmente zwischen Tool-Calls ─────────────────
# L'agent produit des segments avec remove_silences/generate_story/
# generate_timeline_from_prompt puis les passe à export_scenes/render_video.
# Persistance à 2 niveaux :
#   - RAM : lookup rapide pour l'itération courante (perdue au restart)
#   - DB `timelines` : persistant, survit au restart, permet reprise ultérieure
_SEGMENT_STASH: dict[str, list[dict]] = {}
_STASH_TIMELINE_PREFIX = "_stash:"  # nom en DB : "_stash:last"


async def _stash_write(stash_id: str, segments: list[dict], db: AsyncSession) -> None:
    """Écrit en RAM + persist dans la table timelines (name = '_stash:{id}')."""
    from backend.core.database import Timeline
    _SEGMENT_STASH[stash_id] = segments
    total = sum(float(s.get("duration", 0)) for s in segments)
    name = f"{_STASH_TIMELINE_PREFIX}{stash_id}"
    # Upsert : cherche timeline existante par nom, sinon crée
    r = await db.execute(select(Timeline).where(Timeline.name == name))
    tl = r.scalar_one_or_none()
    payload = {
        "segmente": segments,
        "gesamtdauer": total,
        "stash_id": stash_id,
    }
    if tl:
        tl.daten = payload
        tl.gesamtdauer = total
    else:
        db.add(Timeline(name=name, daten=payload, gesamtdauer=total))
    await db.commit()


async def _stash_read(stash_id: str, db: AsyncSession) -> list[dict] | None:
    """Lit d'abord la RAM, puis la DB si absent."""
    from backend.core.database import Timeline
    if stash_id in _SEGMENT_STASH:
        return _SEGMENT_STASH[stash_id]
    name = f"{_STASH_TIMELINE_PREFIX}{stash_id}"
    r = await db.execute(select(Timeline).where(Timeline.name == name))
    tl = r.scalar_one_or_none()
    if tl and isinstance(tl.daten, dict):
        segs = tl.daten.get("segmente") or []
        if segs:
            _SEGMENT_STASH[stash_id] = segs  # recharge en RAM
            return segs
    return None


async def _tool_generate_timeline_from_prompt(args: dict, db: AsyncSession) -> dict:
    """Kern der Bachelorarbeit: Timeline aus natürlicher Beschreibung generieren.

    Pipeline Plan → Retrieve → Assemble. Nutzt qwen2.5:14b für Plan, CLIP + DB
    für Retrieval, und Heuristik oder qwen für Assemble. Segmente werden im
    Stash 'last' gespeichert (kompatibel mit render_video / export_scenes).
    """
    from backend.core.timeline_generator import (
        assemble_timeline as tg_assemble,
        plan_timeline as tg_plan,
        retrieve_candidates as tg_retrieve,
        summarize_pool as tg_summarize_pool,
        _log_stage,
    )

    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt fehlt (natürliche Beschreibung des gewünschten Cuts)"}
    try:
        duration_s = float(args.get("duration_s") or 60.0)
    except (TypeError, ValueError):
        return {"error": "duration_s muss eine Zahl sein"}
    if duration_s < 3 or duration_s > 600:
        return {"error": "duration_s muss zwischen 3 und 600 Sekunden liegen"}

    raw_clip_ids = args.get("clip_ids") or []
    if isinstance(raw_clip_ids, str):
        raw_clip_ids = [raw_clip_ids]
    raw_clip_ids = [str(x).strip() for x in raw_clip_ids if x]
    is_wildcard = any(x in ("*", "all", "tous", "alle") for x in raw_clip_ids)
    if raw_clip_ids and not is_wildcard:
        pool_ids = await _resolve_clip_ids(db, raw_clip_ids)
    else:
        pool_ids = []
    if not pool_ids:
        r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
        pool_ids = [str(cid) for (cid,) in r.all()]
    if not pool_ids:
        return {"error": "keine analysierten Clips im Projekt"}

    assemble_mode = args.get("assemble_mode") or "heuristic"
    if assemble_mode not in ("heuristic", "llm"):
        assemble_mode = "heuristic"
    top_k = int(args.get("top_k") or 5)

    run_id = f"agent_{int(time.time())}"

    try:
        pool_summary = await tg_summarize_pool(db, pool_ids)
        _log_stage("00_pool_summary", pool_summary, run_id)

        plan = await tg_plan(prompt, duration_s, args.get("num_slots_hint"),
                             pool_summary=pool_summary)
        _log_stage("01_plan", plan, run_id)

        candidates = await tg_retrieve(plan, pool_ids, db, top_k=top_k)
        _log_stage("02_candidates", candidates, run_id)

        timeline = await tg_assemble(plan, candidates, mode=assemble_mode,
                                     target_duration_s=duration_s)
        _log_stage("03_timeline", timeline, run_id)
    except Exception as e:
        logger.exception("generate_timeline_from_prompt failed")
        return {"error": f"Pipeline-Fehler: {e}", "run_id": run_id}

    segments = timeline["segments"]
    if not segments:
        return {
            "error": "keine Segmente produziert (Retrieval hat für alle Slots leere Ergebnisse)",
            "run_id": run_id,
            "plan_summary": {
                "narrative": plan.get("narrative_intent_de"),
                "slot_count": len(plan.get("slots") or []),
            },
        }

    await _stash_write("last", segments, db)
    meta = timeline["_meta"]
    return {
        "run_id": run_id,
        "narrative_intent_de": plan.get("narrative_intent_de"),
        "slot_count": len(plan.get("slots") or []),
        "segment_count": meta["segment_count"],
        "total_duration_s": meta["total_duration_s"],
        "target_duration_s": duration_s,
        "skipped_slots": meta["skipped_slots"],
        "assemble_mode": meta["mode"],
        "segments": segments,           # complet → ghost overlay (traceToProposals)
        "segments_preview": segments[:5],
        "stash_id": "last",
        "log_dir": f"backend/outputs/timeline_gen_logs/{run_id}/",
        "_hint": "Segmente im Stash 'last'. Weiter mit render_video / export_scenes / export_last_cleanup.",
    }


async def _tool_generate_story(args: dict, db: AsyncSession) -> dict:
    """Material-first : baut aus dem VORHANDENEN Material die kohärenteste kurze
    Geschichte — ohne Prompt. Erfindet nichts, was nicht im Pool ist. Ideal für
    einen ersten Rohschnitt. Segmente landen im Stash 'last'."""
    from backend.core.timeline_generator import generate_story_from_pool, _log_stage

    duration_s = args.get("duration_s")
    try:
        duration_s = float(duration_s) if duration_s is not None else None
    except (TypeError, ValueError):
        duration_s = None

    raw_clip_ids = args.get("clip_ids") or []
    if isinstance(raw_clip_ids, str):
        raw_clip_ids = [raw_clip_ids]
    raw_clip_ids = [str(x).strip() for x in raw_clip_ids if x]
    is_wildcard = any(x in ("*", "all", "tous", "alle") for x in raw_clip_ids)
    if raw_clip_ids and not is_wildcard:
        pool_ids = await _resolve_clip_ids(db, raw_clip_ids)
    else:
        pool_ids = []
    if not pool_ids:
        r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
        pool_ids = [str(cid) for (cid,) in r.all()]
    if not pool_ids:
        return {"error": "keine analysierten Clips im Projekt"}

    run_id = f"story_{int(time.time())}"
    try:
        story = await generate_story_from_pool(db, pool_ids, target_duration_s=duration_s)
        _log_stage("story", story, run_id)
    except Exception as e:
        logger.exception("generate_story failed")
        return {"error": f"Story-Pipeline-Fehler: {e}", "run_id": run_id}

    segments = story["segments"]
    if not segments:
        return {"error": "keine Story aus dem Material generierbar", "run_id": run_id}

    await _stash_write("last", segments, db)
    meta = story["_meta"]
    return {
        "run_id": run_id,
        "story_title": story.get("story_title"),
        "narrative_intent_de": story.get("narrative_intent_de"),
        "segment_count": meta["segment_count"],
        "total_duration_s": meta["total_duration_s"],
        "pool_size": meta["pool_size"],
        "segments": segments,           # complet → ghost overlay (traceToProposals)
        "segments_preview": segments[:5],
        "stash_id": "last",
        "log_dir": f"backend/outputs/timeline_gen_logs/{run_id}/",
        "_hint": "Erster Rohschnitt im Stash 'last'. Weiter mit render_video / export_scenes.",
    }


async def _tool_render_video(args: dict, db: AsyncSession) -> dict:
    """Rend un MP4 depuis des segments avec aspect ratio et optional subtitles burnt."""
    from backend.core.render import render_mp4, _srt_from_whisper_segments

    stash_id = args.get("stash_id", "last")
    segments = args.get("segments") or await _stash_read(stash_id, db)
    if not segments:
        return {"error": "no segments (pass segments or use stash from a previous tool)"}

    aspect = args.get("aspect_ratio", "16:9")
    name = args.get("name", "CinAssist_Render")
    burn_subs = bool(args.get("burn_subtitles", False))

    srt_str: str | None = None
    if burn_subs:
        # Concatène les segments Whisper de toutes les scènes utilisées, réalignés
        # sur la timeline finale (offset cumulatif de chaque segment).
        # On charge les scènes correspondantes en DB via src_scene_id.
        scene_ids = [s.get("src_scene_id") for s in segments if s.get("src_scene_id")]
        srt_pieces: list[str] = []
        cumulative = 0.0
        if scene_ids:
            r = await db.execute(select(Szene).where(Szene.id.in_(scene_ids)))
            scene_by_id = {str(sc.id): sc for sc in r.scalars().all()}
            for seg in segments:
                sid = seg.get("src_scene_id")
                sc = scene_by_id.get(str(sid)) if sid else None
                if sc and sc.transkription_json:
                    raw = sc.transkription_json
                    if isinstance(raw, dict):
                        raw = raw.get("segmente") or raw.get("segments") or []
                    # Filter segments qui tombent dans [media_start, media_start+duration]
                    seg_start = seg["media_start"]
                    seg_end = seg_start + seg["duration"]
                    speech: list[dict] = []
                    for w in raw:
                        if isinstance(w, dict):
                            ws = float(w.get("start", 0))
                            we = float(w.get("end", 0))
                            if we > seg_start and ws < seg_end:
                                # Décale à la timeline finale
                                speech.append({
                                    "start": max(0, ws - seg_start),
                                    "end": max(0, we - seg_start),
                                    "text": w.get("text", ""),
                                })
                    if speech:
                        srt_pieces.append(_srt_from_whisper_segments(speech, time_offset=cumulative))
                cumulative += seg["duration"]
        srt_str = "\n".join(srt_pieces) or None

    try:
        info = render_mp4(
            segments,
            aspect_ratio=aspect,
            name=name,
            subtitles_srt=srt_str,
        )
    except Exception as e:
        logger.exception("render_video failed")
        return {"error": f"render failed: {e}"}
    return info


async def _tool_detect_beats(args: dict, db: AsyncSession) -> dict:
    """Erkennt BPM und Beat-Positionen in einem Clip (via librosa). Persona Musikvideo-Cutter."""
    from backend.core.render import detect_beats

    clip_id = args.get("clip_id") or args.get("clip_name")
    if not clip_id:
        return {"error": "clip_id (UUID) or clip_name is required"}
    # Résoudre nom → UUID si nécessaire
    resolved = await _resolve_clip_ids(db, [clip_id])
    if not resolved:
        return {"error": f"clip '{clip_id}' not found (neither UUID nor filename match)"}
    r = await db.execute(select(Clip).where(Clip.id == resolved[0]))
    clip = r.scalar_one_or_none()
    if not clip or not clip.dateipfad:
        return {"error": f"clip {clip_id} not found"}
    result = detect_beats(clip.dateipfad)
    # Truncate beat_times si trop long
    if "beat_times_s" in result and len(result["beat_times_s"]) > 30:
        result["beat_times_preview"] = result["beat_times_s"][:15]
        result["beat_times_full_count"] = len(result["beat_times_s"])
        del result["beat_times_s"]
    return result


async def _tool_rediarize_clip(args: dict, db: AsyncSession) -> dict:
    """Refait la diarization d'un clip avec des hints (num_speakers, min_speaker_time).

    Utile quand pyannote sur-segmente : ex. MLK "I Have A Dream" détecté avec
    3 speakers alors qu'il n'y a qu'un orateur → passe `num_speakers=1`.

    Remplace en DB les rows Speaker/SceneSpeaker existantes du clip.
    """
    from sqlalchemy import delete as sql_delete
    from backend.core.database import SceneSpeaker as SS, Speaker as SP
    from backend.core.diarize import diarize_audio, summarize_by_speaker, match_speakers_to_scenes
    import subprocess, tempfile, os

    clip_arg = args.get("clip_id") or args.get("clip_name")
    if not clip_arg:
        return {"error": "clip_id (UUID) or clip_name is required"}
    resolved = await _resolve_clip_ids(db, [clip_arg])
    if not resolved:
        return {"error": f"clip '{clip_arg}' not found"}
    clip_id = resolved[0]
    r = await db.execute(select(Clip).where(Clip.id == clip_id))
    clip = r.scalar_one_or_none()
    if not clip or not clip.dateipfad:
        return {"error": f"clip {clip_id} has no file path"}

    num_speakers = args.get("num_speakers")
    min_speakers = args.get("min_speakers")
    max_speakers = args.get("max_speakers")
    min_speaker_time_s = float(args.get("min_speaker_time_s") or 3.0)

    # Extract audio to a temp WAV mono 16kHz (pyannote-friendly)
    # NamedTemporaryFile évite le TOCTOU race de mktemp() deprecated.
    from backend.core.config import TEMP_DIR
    with tempfile.NamedTemporaryFile(prefix="cinassist_rediarize_", suffix=".wav",
                                     dir=str(TEMP_DIR), delete=False) as tf:
        tmp_audio = tf.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", clip.dateipfad, "-vn", "-ac", "1", "-ar", "16000",
             "-f", "wav", tmp_audio],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not os.path.exists(tmp_audio):
            return {"error": f"audio extraction failed: {proc.stderr.decode('utf-8', 'replace')[-300:]}"}

        result = diarize_audio(
            tmp_audio,
            num_speakers=int(num_speakers) if num_speakers else None,
            min_speakers=int(min_speakers) if min_speakers else None,
            max_speakers=int(max_speakers) if max_speakers else None,
            min_speaker_time_s=min_speaker_time_s,
        )
    finally:
        try: os.unlink(tmp_audio)
        except OSError: pass

    if not result.get("available"):
        return {"error": result.get("error") or "diarization not available"}

    # Purge et re-persist
    await db.execute(sql_delete(SS).where(
        SS.speaker_id.in_(select(SP.id).where(SP.clip_id == clip_id))
    ))
    await db.execute(sql_delete(SP).where(SP.clip_id == clip_id))
    await db.flush()

    summary = summarize_by_speaker(result["segments"])
    speaker_by_label: dict[str, SP] = {}
    for label, agg in summary.items():
        sp = SP(
            clip_id=clip_id,
            label_auto=label,
            total_speaking_time=agg["total_time"],
            segment_count=agg["segment_count"],
        )
        db.add(sp)
        speaker_by_label[label] = sp
    await db.flush()

    # Réassigne scene_speakers
    r2 = await db.execute(
        select(Szene).where(Szene.clip_id == clip_id).order_by(Szene.szenen_nr)
    )
    scenes = r2.scalars().all()
    scene_ranges = [(float(s.start_zeit), float(s.end_zeit)) for s in scenes]
    overlaps = match_speakers_to_scenes(result["segments"], scene_ranges)
    for szene, by_speaker in zip(scenes, overlaps):
        for label, dur in by_speaker.items():
            sp = speaker_by_label.get(label)
            if sp and dur > 0:
                db.add(SS(scene_id=szene.id, speaker_id=sp.id, speaking_time=dur))
    await db.commit()

    return {
        "clip_id": clip_id,
        "clip_name": clip.dateiname,
        "total_speakers": result["total_speakers"],
        "filtered_out_below_min_time": result["filtered_out"],
        "hint_used": result["hint_used"],
        "min_speaker_time_s": min_speaker_time_s,
        "speakers": [
            {
                "label_auto": sp.label_auto,
                "total_speaking_time_s": round(sp.total_speaking_time or 0.0, 2),
                "segment_count": sp.segment_count,
            }
            for sp in speaker_by_label.values()
        ],
    }


async def _tool_retranscribe_clip(args: dict, db: AsyncSession) -> dict:
    """Refait la transcription Whisper d'un clip existant avec language=auto.

    Utile après le fix language=None : les clips ingérés avant ont été forcés
    en 'de' et les vidéos EN (Snowden, MLK) n'avaient pas de transcription.
    Met à jour transkription + transkription_json des scènes overlapping avec
    les segments Whisper. Ne touche pas au reste (CLIP, faces, speakers).
    """
    from backend.core.config import WHISPER_MODEL, TEMP_DIR
    import subprocess, os

    clip_arg = args.get("clip_id") or args.get("clip_name")
    if not clip_arg:
        return {"error": "clip_id (UUID) or clip_name is required"}
    resolved = await _resolve_clip_ids(db, [clip_arg])
    if not resolved:
        return {"error": f"clip '{clip_arg}' not found"}
    clip_id = resolved[0]
    r = await db.execute(select(Clip).where(Clip.id == clip_id))
    clip = r.scalar_one_or_none()
    if not clip or not clip.dateipfad:
        return {"error": f"clip {clip_id} has no file path"}

    language = args.get("language")  # None → auto-detect (recommandé)

    # Pré-check : le clip a-t-il de l'audio ?
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", clip.dateipfad],
        capture_output=True, text=True, timeout=10,
    )
    if not (probe.returncode == 0 and "audio" in (probe.stdout or "").lower()):
        return {
            "clip_id": clip_id,
            "clip_name": clip.dateiname,
            "skipped": True,
            "reason": "Clip hat keine Audiospur — Transkription übersprungen.",
        }

    with tempfile.NamedTemporaryFile(prefix="cinassist_retrans_", suffix=".wav",
                                     dir=str(TEMP_DIR), delete=False) as tf:
        tmp_audio = tf.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", clip.dateipfad, "-vn", "-ac", "1", "-ar", "16000",
             "-f", "wav", tmp_audio],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0 or not os.path.exists(tmp_audio):
            return {"error": f"audio extraction failed: {proc.stderr.decode('utf-8', 'replace')[-300:]}"}

        try:
            import mlx_whisper
        except ImportError:
            return {"error": "mlx_whisper not installed"}

        result = mlx_whisper.transcribe(
            tmp_audio,
            path_or_hf_repo=WHISPER_MODEL,
            language=language,
            word_timestamps=True,
        )
    finally:
        try: os.unlink(tmp_audio)
        except OSError: pass

    text = (result.get("text") or "").strip()
    detected_lang = result.get("language", "?")
    segments = [{
        "start": round(seg["start"], 3),
        "end": round(seg["end"], 3),
        "text": seg["text"].strip(),
        "woerter": [
            {"wort": w["word"].strip(),
             "start": round(w["start"], 3),
             "end": round(w["end"], 3)}
            for w in seg.get("words", [])
        ],
    } for seg in result.get("segments", [])]

    # Mettre à jour les szenen : trouver chaque overlap
    r2 = await db.execute(select(Szene).where(Szene.clip_id == clip_id).order_by(Szene.szenen_nr))
    scenes = r2.scalars().all()
    updated = 0
    for sc in scenes:
        seg_text = ""
        seg_json = []
        for seg in segments:
            if seg["start"] < float(sc.end_zeit) and seg["end"] > float(sc.start_zeit):
                seg_text += seg["text"] + " "
                seg_json.append(seg)
        new_text = seg_text.strip() or None
        if new_text != sc.transkription:
            sc.transkription = new_text
            sc.transkription_json = seg_json or None
            updated += 1
    await db.commit()

    return {
        "clip_id": clip_id,
        "clip_name": clip.dateiname,
        "detected_language": detected_lang,
        "text_length_chars": len(text),
        "segment_count": len(segments),
        "scenes_updated": updated,
        "text_preview": text[:300],
    }


async def _tool_cluster_speakers_across_clips(args: dict, db: AsyncSession) -> dict:
    """Erkennt dieselbe Person cross-clip via pyannote-Voice-Embeddings + Cosine-Clustering.

    Sammelt für jeden speaker den längsten Rede-Ausschnitt, berechnet den 512-dim
    Voice-Embedding, gruppiert per Cosine-Similarity (Threshold default 0.75).
    """
    from backend.core.speaker_cluster import cluster_speakers
    from backend.core.config import TEMP_DIR

    threshold = float(args.get("similarity_threshold") or 0.75)
    apply_labels = bool(args.get("apply_labels", False))

    # Alle Speaker + zugehörige Szenen holen
    r = await db.execute(
        select(Speaker, Clip.dateiname, Clip.dateipfad)
        .join(Clip, Clip.id == Speaker.clip_id)
        .where(Speaker.total_speaking_time.isnot(None))
    )
    rows = r.all()
    if len(rows) < 2:
        return {"error": "mindestens 2 Speaker mit Redezeit erforderlich"}

    # Pour chaque speaker, trouve la scene où il parle le plus longtemps
    speakers_info: list[dict] = []
    for sp, dateiname, dateipfad in rows:
        if not dateipfad:
            continue
        r2 = await db.execute(
            select(SceneSpeaker, Szene)
            .join(Szene, Szene.id == SceneSpeaker.scene_id)
            .where(SceneSpeaker.speaker_id == sp.id)
            .order_by(SceneSpeaker.speaking_time.desc())
            .limit(1)
        )
        best = r2.first()
        if not best:
            continue
        _, sc = best
        speakers_info.append({
            "speaker_id": str(sp.id),
            "clip_id": str(sp.clip_id),
            "clip_name": dateiname,
            "clip_path": dateipfad,
            "start_s": float(sc.start_zeit),
            "duration_s": min(float(sc.dauer), 20.0),  # max 20s pour l'embedding
            "label_auto": sp.label_auto,
            "label_manual": sp.label_manual,
        })

    if len(speakers_info) < 2:
        return {"error": f"nur {len(speakers_info)} Speaker mit auswertbarem Ausschnitt"}

    result = cluster_speakers(speakers_info, TEMP_DIR, similarity_threshold=threshold)

    if apply_labels and "clusters" in result:
        # Applique le suggested_label comme label_manual à tous les speakers du cluster
        renamed = 0
        for cluster in result["clusters"]:
            if cluster["speaker_count"] < 2:
                continue  # Ne pas renommer les speakers seuls
            new_label = cluster["suggested_label"]
            for sid in cluster["speaker_ids"]:
                r3 = await db.execute(select(Speaker).where(Speaker.id == sid))
                sp = r3.scalar_one_or_none()
                if sp and sp.label_manual != new_label:
                    sp.label_manual = new_label
                    renamed += 1
        await db.commit()
        result["speakers_renamed"] = renamed

    return result


async def _tool_sync_multicam(args: dict, db: AsyncSession) -> dict:
    """Berechnet Audio-Sync-Offsets zwischen mehreren Multicam-Clips (Master-Winkel + weitere).

    Für Interview- / Doku-Setups mit mehreren Kameras, die zeitgleich aufnehmen
    und dasselbe Audio (mit leichten Positionsverschiebungen) haben.
    """
    from backend.core.multicam_sync import sync_clips
    from backend.core.config import TEMP_DIR

    raw_ids = args.get("clip_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if len(raw_ids) < 2:
        return {"error": "sync benötigt mindestens 2 clip_ids"}

    resolved = await _resolve_clip_ids(db, raw_ids)
    if len(resolved) < 2:
        return {"error": f"nur {len(resolved)} Clip(s) gefunden für {raw_ids}"}

    r = await db.execute(select(Clip).where(Clip.id.in_(resolved)))
    clips_by_id = {str(c.id): c for c in r.scalars().all()}
    clip_paths = []
    for cid in resolved:
        c = clips_by_id.get(cid)
        if c and c.dateipfad:
            clip_paths.append((cid, c.dateipfad))

    if len(clip_paths) < 2:
        return {"error": "zu wenige Clips mit Dateipfad"}

    result = sync_clips(clip_paths, TEMP_DIR)
    # Bereichere mit Dateinamen für Lesbarkeit
    for entry in result.get("offsets", []):
        c = clips_by_id.get(entry.get("clip_id"))
        if c:
            entry["clip_name"] = c.dateiname
    master = clips_by_id.get(result.get("master_clip_id"))
    if master:
        result["master_clip_name"] = master.dateiname
    return result


async def _tool_export_last_cleanup(args: dict, db: AsyncSession) -> dict:
    """Exportiert das Ergebnis des letzten remove_silences (via Stash) als FCPXML."""
    stash_id = args.get("stash_id", "last")
    fmt = (args.get("format") or "fcpxml").lower()
    name = args.get("name", "CinAssist_Cleaned")
    segments = await _stash_read(stash_id, db)
    if not segments:
        return {"error": f"no stashed segments for id '{stash_id}'. Run remove_silences first."}
    if fmt not in ("fcpxml", "otio"):
        return {"error": f"format must be 'fcpxml' or 'otio'"}
    try:
        info = _otio_export_to_file(segments, format=fmt, name=name)
    except Exception as e:
        return {"error": f"Export failed: {e}"}
    info["segment_count"] = len(segments)
    return info


# ─── Registry ────────────────────────────────────────────────
TOOLS: dict[str, Tool] = {
    "list_clips": Tool(
        name="list_clips",
        description="Listet alle im Projekt hochgeladenen Video-Clips mit Dauer und Anzahl Szenen.",
        args_schema={},
        handler=_tool_list_clips,
    ),
    "search_scenes_by_prompt": Tool(
        name="search_scenes_by_prompt",
        description="Sucht Szenen anhand einer natürlichsprachlichen Beschreibung (semantische CLIP-Textsuche). Beispiele: 'wide drone shot', 'person talking close-up', 'coffee being poured'.",
        args_schema={
            "query": "str — Beschreibung auf Deutsch oder Englisch dessen, was gesucht wird",
            "limit": "int — max. Anzahl Ergebnisse (Default 5)",
        },
        handler=_tool_search_scenes_by_prompt,
    ),
    "list_speakers": Tool(
        name="list_speakers",
        description="Listet alle im Projekt identifizierten Sprecher (unterscheidbare Stimmen). Jeder hat ein label_auto (SPEAKER_00) und ggf. ein label_manual ('Anna'), falls vom Nutzer umbenannt.",
        args_schema={},
        handler=_tool_list_speakers,
    ),
    "filter_by_speaker": Tool(
        name="filter_by_speaker",
        description="Gibt die Szenen zurück, in denen ein bestimmter Sprecher vorkommt. Nutze den exakten Namen (label_manual wie 'Anna' oder label_auto wie 'SPEAKER_00').",
        args_schema={
            "speaker": "str — Name des Sprechers (Anna, Marc, SPEAKER_00…)",
            "limit": "int — max. Anzahl Szenen (Default 20)",
        },
        handler=_tool_filter_by_speaker,
    ),
    "rename_speaker": Tool(
        name="rename_speaker",
        description="Benennt einen Sprecher um (gibt SPEAKER_00 einen echten Namen). Der Nutzer hört eine Szene und sagt dir 'SPEAKER_00 ist Anna' → du rufst dieses Tool auf.",
        args_schema={
            "speaker_id": "str — UUID oder label_auto (z. B. 'SPEAKER_00') aus list_speakers",
            "new_name": "str — neuer lesbarer Name ('Anna', 'Marc'…)",
        },
        handler=_tool_rename_speaker,
    ),
    "filter_by_framing": Tool(
        name="filter_by_framing",
        description="Filtert Szenen nach Bildeinstellung (Framing). Akzeptiert eine oder mehrere Einstellungen auf einmal. Bsp.: für 'Nahaufnahmen ODER Halbtotale' übergib ['closeup','medium'].",
        args_schema={
            "framing": "str | list[str] — eines oder mehrere von: extreme_closeup, closeup, medium, wide_with_person, wide_no_person",
            "min_faces": "int — Mindestanzahl Gesichter (Default 0)",
            "limit": "int — max. Anzahl zurückgegebener Szenen (Default 20)",
        },
        handler=_tool_filter_by_framing,
    ),
    "export_scenes": Tool(
        name="export_scenes",
        description="Exportiert Szenen ODER benutzerdefinierte Segmente nach FCPXML/OTIO (Premiere/FCP/Resolve). Datei in ~/Documents/CinAssist_Exports/. Nutze scene_ids für ganze Szenen, oder segments für Custom (z. B. aus remove_silences).",
        args_schema={
            "scene_ids": "list[str] — UUIDs der Szenen in gewünschter chronologischer Reihenfolge (einfacher Modus)",
            "segments": "list[dict] — Custom-Segmente {clip_path, clip_name, media_start, duration} (erweiterter Modus)",
            "format": "str — 'fcpxml' (Premiere/FCP/Resolve) oder 'otio' (JSON OpenTimelineIO)",
            "name": "str — Name der Ausgabedatei",
        },
        handler=_tool_export_scenes,
    ),
    "remove_silences": Tool(
        name="remove_silences",
        description="Entfernt lange Stillen aus den angegebenen Szenen oder Clips. Gibt exportbereite Sprech-Segmente zurück (kombinierbar mit export_last_cleanup). Nutzt Whisper-Timestamps aus der DB. Feature Nr. 1 für Profi-Cutter (Descript-artig).",
        args_schema={
            "scene_ids": "list[str] — konkrete Szenen (mutex zu clip_ids)",
            "clip_ids": "list[str] — kompletter Clip (mutex zu scene_ids)",
            "min_silence_ms": "int — Mindestdauer einer Stille zum Schneiden (Default 800)",
            "keep_margin_ms": "int — vorher/nachher zu behaltender Puffer (Default 150)",
        },
        handler=_tool_remove_silences,
    ),
    "find_hesitations": Tool(
        name="find_hesitations",
        description="Erkennt 'ähm', 'euh', 'um', unmittelbare Wiederholungen in den Transkripten. Nützlich, um vor einem echten Cut abzuschätzen, wie viel Zeit gespart werden kann.",
        args_schema={
            "scene_ids": "list[str] — konkrete Szenen (mutex zu clip_ids)",
            "clip_ids": "list[str] — kompletter Clip",
        },
        handler=_tool_find_hesitations,
    ),
    "render_video": Tool(
        name="render_video",
        description="Rendert ein finales MP4 aus Segmenten (direkt übergeben oder aus dem letzten Stash). Unterstützt Seitenverhältnis (16:9, 9:16, 1:1) und Whisper-Untertitel als Burnt-in. Für Social-Deliverables oder Preview.",
        args_schema={
            "segments": "list[dict] — Custom-Segmente (optional, falls Stash vorhanden)",
            "stash_id": "str — ID des zu nutzenden Stashes (Default 'last')",
            "aspect_ratio": "str — '16:9' (Default) | '9:16' (mobil) | '1:1' (quadratisch)",
            "name": "str — Name der Ausgabedatei",
            "burn_subtitles": "bool — Whisper-Untertitel ins Video brennen (Default false)",
        },
        handler=_tool_render_video,
    ),
    "detect_beats": Tool(
        name="detect_beats",
        description="Analysiert einen Clip auf BPM und Beat-Positionen (librosa). Nützlich, um Schnitte auf die Musik zu setzen (Persona Musikvideo-Cutter).",
        args_schema={
            "clip_id": "str — UUID oder Name des zu analysierenden Clips",
        },
        handler=_tool_detect_beats,
    ),
    "generate_timeline_from_prompt": Tool(
        name="generate_timeline_from_prompt",
        description="Generiert eine strukturierte Timeline aus einer natürlichen Beschreibung (Plan → Retrieve → Assemble). Der LLM plant zuerst narrative Slots (Einstellungen mit Framing, Dauer, Speaker-Bedarf), das System sucht dann für jeden Slot die passendsten Szenen via CLIP-Retrieval, und wählt/schneidet zusammen. Kern der wissenschaftlichen Arbeit — nutze dies, wenn der Nutzer den gewünschten Schnitt konkret BESCHREIBT. (Für einen ersten Schnitt OHNE Beschreibung nutze stattdessen generate_story.) Segmente landen im Stash 'last' (weiter mit render_video / export_scenes).",
        args_schema={
            "prompt": "str — natürliche Beschreibung des gewünschten Cuts (DE/EN/FR)",
            "duration_s": "float — Zieldauer in Sekunden (Default 60, Bereich 3-600)",
            "clip_ids": "list[str] — zu nutzende Clips (Default: alle analysierten Clips)",
            "num_slots_hint": "int — Richtwert für Anzahl Slots (optional, sonst automatisch)",
            "top_k": "int — Anzahl Kandidaten pro Slot vor Auswahl (Default 5)",
            "assemble_mode": "str — 'heuristic' (schnell, top-1 + Zentrum, Default) | 'llm' (qwen picke)",
        },
        handler=_tool_generate_timeline_from_prompt,
    ),
    "generate_story": Tool(
        name="generate_story",
        description="Material-first: baut OHNE Prompt aus dem VORHANDENEN Material die kohärenteste kurze Geschichte / den ersten Rohschnitt. Der LLM sieht das gesamte reale Rohmaterial (Szenenbeschreibungen, Cadrage, Dauer) und ordnet daraus einen roten Faden (Anfang→Entwicklung→Schluss) — erfindet NICHTS, was nicht existiert. Ideal wenn der Nutzer sagt 'mach mir einen ersten Schnitt / erzähl eine Geschichte mit dem was da ist'. Komplementär zu generate_timeline_from_prompt (das ist prompt-getrieben). Segmente landen im Stash 'last'.",
        args_schema={
            "duration_s": "float — ungefähre Zieldauer in Sekunden (optional)",
            "clip_ids": "list[str] — zu nutzende Clips (Default: alle analysierten Clips)",
        },
        handler=_tool_generate_story,
    ),
    "retranscribe_clip": Tool(
        name="retranscribe_clip",
        description="Wiederholt die Whisper-Transkription eines Clips mit language='auto' (Default). Nützlich für Clips, die vor dem language=None-Fix mit erzwungener DE-Sprache transkribiert wurden (z. B. englische Videos wie Snowden/MLK, die dadurch leer blieben). Aktualisiert transkription der Szenen. Ändert nichts an CLIP-Embeddings, Faces oder Speakern.",
        args_schema={
            "clip_id": "str — UUID oder Name des Clips",
            "language": "str — Sprach-Hint (z. B. 'en', 'de', 'fr'). Default: null → auto-detect.",
        },
        handler=_tool_retranscribe_clip,
    ),
    "rediarize_clip": Tool(
        name="rediarize_clip",
        description="Wiederholt die Diarization eines Clips mit einem Hint zur Sprecheranzahl. Nützlich, wenn pyannote über-segmentiert (z. B. 3 Sprecher in einer Ein-Personen-Rede erkannt → num_speakers=1). Ersetzt bestehende speakers und scene_speakers.",
        args_schema={
            "clip_id": "str — UUID oder Name des Clips",
            "num_speakers": "int — EXAKTE erwartete Sprecheranzahl (Priorität)",
            "min_speakers": "int — Untergrenze, falls num_speakers unbekannt",
            "max_speakers": "int — Obergrenze, falls num_speakers unbekannt",
            "min_speaker_time_s": "float — entfernt Sprecher mit < X Sek. Redezeit gesamt (Default 3.0, Anti-Falschpositive)",
        },
        handler=_tool_rediarize_clip,
    ),
    "cluster_speakers_across_clips": Tool(
        name="cluster_speakers_across_clips",
        description="Erkennt dieselbe Person clip-übergreifend via pyannote-Voice-Embeddings. Für Interviews mit mehreren Aufnahmen der gleichen Personen. Optional Auto-Rename aller Speaker eines Clusters mit einem gemeinsamen Label.",
        args_schema={
            "similarity_threshold": "float — Cosine-Threshold zum Mergen (Default 0.75, höher = strenger)",
            "apply_labels": "bool — Auto-Rename der geclusterten Speaker mit gemeinsamem label_manual (Default false → nur Preview)",
        },
        handler=_tool_cluster_speakers_across_clips,
    ),
    "sync_multicam": Tool(
        name="sync_multicam",
        description="Berechnet zeitliche Offsets zwischen mehreren Multicam-Clips über Audio-Kreuzkorrelation (FFT). Für Interview-/Doku-Setups mit mehreren Kameras. Erster Clip = Master (offset=0), andere Clips relativ dazu. Gibt confidence pro Paar zurück.",
        args_schema={
            "clip_ids": "list[str] — mind. 2 Clip-UUIDs oder -Namen (erster wird Master)",
        },
        handler=_tool_sync_multicam,
    ),
    "export_last_cleanup": Tool(
        name="export_last_cleanup",
        description="Exportiert das Ergebnis des letzten remove_silences (im RAM gestashte Segmente) nach FCPXML. Direkt nach remove_silences zu verwenden.",
        args_schema={
            "stash_id": "str — Stash-ID (Default 'last')",
            "format": "str — 'fcpxml' oder 'otio' (Default fcpxml)",
            "name": "str — Name der Ausgabedatei",
        },
        handler=_tool_export_last_cleanup,
    ),
}


# ─── System prompt ───────────────────────────────────────────
def _build_system_prompt(timeline_state: dict | None = None) -> str:
    tools_desc = []
    for t in TOOLS.values():
        args_str = ", ".join(f"{k}: {v}" for k, v in t.args_schema.items()) or "aucun"
        tools_desc.append(f"- {t.name}({args_str})\n    {t.description}")
    tools_block = "\n".join(tools_desc)

    # Snapshot de la timeline actuelle (envoyé par le frontend). Permet à l'agent
    # de raisonner sur "aktueller Clip", "V1", "der erste Clip" sans avoir à
    # appeler list_clips systématiquement.
    tl_block = ""
    if timeline_state and isinstance(timeline_state, dict):
        clips = timeline_state.get("clips") or []
        total = timeline_state.get("totalDuration") or 0
        n_v = timeline_state.get("numVideoTracks") or 0
        n_a = timeline_state.get("numAudioTracks") or 0
        playhead = timeline_state.get("playheadTime") or 0
        selected_ids = timeline_state.get("selectedTlIds") or []
        if clips:
            def _resolve_current_clips() -> list[dict]:
                # Priorité 1 : les clips sélectionnés (selectedTlIds)
                if selected_ids:
                    sel = [c for c in clips if c.get("tlId") in selected_ids]
                    if sel:
                        return sel
                # Priorité 2 : le clip sous le playhead (piste vidéo de priorité la + haute)
                at_ph = [
                    c for c in clips
                    if c.get("start", 0) <= playhead <= c.get("start", 0) + c.get("duration", 0)
                ]
                if at_ph:
                    at_ph.sort(key=lambda c: c.get("videoTrackIndex", 0))
                    return [at_ph[0]]
                return []

            current = _resolve_current_clips()
            current_note = ""
            if current:
                names = ", ".join(c.get("name") or c.get("clipId") or "?" for c in current)
                ids = ", ".join(c.get("clipId") or "?" for c in current)
                current_note = (
                    f"\n>>> AKTUELLER CLIP (dieser ist gemeint wenn Nutzer 'aktueller Clip' / 'dieser Clip' sagt): "
                    f"{names}  (clipId(s): {ids})"
                )

            lines = []
            for c in clips[:40]:
                v_idx = c.get("videoTrackIndex", 0) or 0
                start = c.get("start", 0)
                dur = c.get("duration", 0)
                name = c.get("name") or c.get("clipId") or "?"
                marker = " ← SELECTED" if c.get("tlId") in selected_ids else ""
                lines.append(
                    f"  - V{int(v_idx)+1}: {name} @ {start:.2f}s → {start+dur:.2f}s (dur {dur:.2f}s, clipId={c.get('clipId')}){marker}"
                )
            more = f"\n  … ({len(clips) - 40} weitere Clips)" if len(clips) > 40 else ""
            tl_block = (
                "\n\n=== Aktuelle Timeline des Nutzers (Kontext) ===\n"
                f"Gesamtdauer: {total:.2f}s · {n_v} V-Spur(en), {n_a} A-Spur(en) · {len(clips)} Clip(s) · "
                f"Playhead @ {playhead:.2f}s"
                f"{current_note}\n"
                f"Clips auf der Timeline:\n" + "\n".join(lines) + more +
                "\n\nWICHTIG: Wenn der Nutzer 'aktueller Clip', 'dieser Clip' oder 'auf der Timeline' sagt, "
                "beziehe dich AUSSCHLIESSLICH auf den oben markierten 'AKTUELLER CLIP'. "
                "Nutze seine `clipId` als Argument für tools (z. B. remove_silences {clip_ids: [<clipId>]}). "
                "KEIN list_clips-Aufruf nötig — du hast die IDs bereits."
            )

    # ── Stil-Präferenzen (Nutzerprofil persistiert per localStorage) ──
    # Le frontend inclut `style_prefs` dans le timeline_state s'ils sont définis.
    # Format attendu (tous optionnels) :
    #   { language, target_duration_sec, cutting_style, framing_mix,
    #     auto_cleanup_silences, auto_remove_hesitations, min_scene_duration_sec }
    style_block = ""
    if timeline_state and isinstance(timeline_state, dict):
        sp = timeline_state.get("style_prefs") or {}
        if sp and isinstance(sp, dict):
            lang_names = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch"}
            style_lines: list[str] = []
            lang = sp.get("language")
            if lang in lang_names:
                style_lines.append(f"- Bevorzugte Antwortsprache: {lang_names[lang]}")
            td = sp.get("target_duration_sec")
            if isinstance(td, (int, float)) and td > 0:
                style_lines.append(f"- Ziel-Dauer für Rohschnitte: {int(td)} s")
            cs = sp.get("cutting_style")
            cs_map = {"fast": "schnell (2-4 s pro Cut)", "moderate": "moderat (5-8 s pro Cut)", "slow": "ruhig (10+ s pro Cut)"}
            if cs in cs_map:
                style_lines.append(f"- Schnittrhythmus: {cs_map[cs]}")
            fm = sp.get("framing_mix")
            if isinstance(fm, dict):
                parts = []
                for k, label in [("closeup", "Close-up"), ("medium", "Medium"), ("wide", "Wide")]:
                    v = fm.get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        parts.append(f"{int(v)}% {label}")
                if parts:
                    style_lines.append(f"- Framing-Mix bevorzugt: {', '.join(parts)}")
            msd = sp.get("min_scene_duration_sec")
            if isinstance(msd, (int, float)) and msd > 0:
                style_lines.append(f"- Mindestszenenlänge: {msd} s")
            if sp.get("auto_cleanup_silences") is not None:
                style_lines.append(f"- Automatischer Cleanup Stille: {'JA' if sp['auto_cleanup_silences'] else 'NEIN'}")
            if sp.get("auto_remove_hesitations") is not None:
                style_lines.append(f"- Automatisches Entfernen von Zögerungen: {'JA' if sp['auto_remove_hesitations'] else 'NEIN'}")
            if style_lines:
                style_block = (
                    "\n\n>>> NUTZER-STIL-PRÄFERENZEN (respektiere diese Werte in allen Vorschlägen und Ausführungen):\n"
                    + "\n".join(style_lines)
                )

    return f"""Du bist CinAssist, ein KI-Agent für Video-Postproduktion. Du hilfst Profi-Cuttern, Routinearbeiten zu automatisieren (Sichtung/Dérushage, Plansuche, Cleanup, Rohschnitt).{tl_block}{style_block}

Du antwortest AUSSCHLIESSLICH in gültigem JSON, niemals Freitext außerhalb des JSON.

Pflichtformat jeder Antwort:
{{
  "thought": "deine Überlegung in einem kurzen deutschen Satz",
  "action": "tool_name" oder "done",
  "args": {{ … Argumente des Tools (Schema einhalten) … }},
  "final_answer": "finale Antwort an den Nutzer auf Deutsch (NUR wenn action=done)"
}}

Verfügbare Tools:
{tools_block}

Regeln:
1. Zerlege komplexe Anfragen in mehrere aufeinanderfolgende Tool-Calls.
2. Nach jedem Tool-Call bekommst du eine "Observation" (das JSON-Ergebnis). Nutze sie, um den nächsten Tool-Call zu entscheiden.
3. Sobald du genug Informationen hast, setze "action": "done" und fülle "final_answer" mit einer klaren deutschen Antwort.
4. Sei prägnant: vermeide redundante Tool-Calls, komm auf den Punkt.
5. Wenn nichts gefunden wird, sag es ehrlich in final_answer, statt zu erfinden.
6. IDs vs. Namen: Die meisten Tools akzeptieren jetzt sowohl UUIDs als auch Dateinamen (z. B. "mlk_1min.mp4" oder nur "mlk_1min"). Falls ein Tool mit "invalid UUID" crasht, unterstützt es die Auflösung noch nicht — rufe dann list_clips auf, um die UUID zu erhalten.
7. Multi-Value-Filter: filter_by_framing akzeptiert mehrere Framings auf einmal als Liste, z. B. framing=["closeup","medium"]. Nicht mehrere separate Calls."""


# ─── Ollama call ─────────────────────────────────────────────
async def _call_ollama(prompt: str) -> tuple[dict, dict]:
    """Retourne (parsed_json, meta) où meta contient latence + tokens/s."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": AGENT_MODEL,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": TEMPERATURE},
            },
        )
        r.raise_for_status()
        data = r.json()
    wall = time.time() - t0
    resp_text = data.get("response", "")
    eval_c = data.get("eval_count", 0)
    eval_d = data.get("eval_duration", 1) / 1e9
    meta = {
        "wall_s": round(wall, 2),
        "tokens": eval_c,
        "tokens_per_s": round(eval_c / eval_d, 1) if eval_d > 0 else 0,
    }
    try:
        parsed = json.loads(resp_text)
    except Exception as e:
        parsed = {
            "thought": f"[Parse error: {e}]",
            "action": "done",
            "final_answer": f"JSON-Parsing-Fehler des Modells: {resp_text[:200]}",
        }
    return parsed, meta


# ─── ReAct loop ──────────────────────────────────────────────
async def _run_agent(user_prompt: str, db: AsyncSession, timeline_state: dict | None = None):
    """Générateur async : yield chaque étape (thought/action/observation/done) au fur et à mesure."""
    system = _build_system_prompt(timeline_state)
    trace_lines = [f"UTILISATEUR: {user_prompt}"]

    unknown_tool_streak = 0

    def _to_text(x) -> str:
        if isinstance(x, str):
            return x.strip()
        if x is None:
            return ""
        if isinstance(x, (dict, list)):
            return json.dumps(x, ensure_ascii=False)
        return str(x).strip()

    async def _synthesize(reason: str) -> str:
        """Zwingt das LLM, eine finale Antwort basierend auf den Observations zu schreiben."""
        synth_prompt = (
            system
            + "\n\n"
            + "\n\n".join(trace_lines)
            + f"\n\nAnweisung ({reason}): Du hast alle nötigen Observations erhalten. "
            "Formuliere JETZT die finale Antwort für den Nutzer auf Deutsch, als JSON, "
            "mit action='done' und nicht-leerem final_answer. Fasse die gesammelten "
            "Infos klar zusammen (Zahlen, Listen, Kernpunkte). Starte KEIN weiteres Tool."
        )
        try:
            sp, _ = await _call_ollama(synth_prompt)
            return _to_text(sp.get("final_answer")) or _to_text(sp.get("thought"))
        except Exception as e:
            logger.warning("synthesis fallback failed: %s", e)
            return ""

    for step in range(MAX_ITERATIONS):
        prompt = system + "\n\n" + "\n\n".join(trace_lines) + "\n\nAntworte jetzt im JSON-Format."
        parsed, meta = await _call_ollama(prompt)
        thought = parsed.get("thought", "")
        action = parsed.get("action", "done")
        args = parsed.get("args", {}) or {}

        # Traite action=null/none/vide comme un signal "je n'ai plus rien à faire"
        if not action or (isinstance(action, str) and action.strip().lower() in ("none", "null", "n/a", "")):
            action = "done"

        yield {"type": "thought", "step": step, "content": thought, "meta": meta}

        # Erkennt das Muster "Schleife auf unbekanntem Tool": nach 2 nicht existierenden
        # Tools in Folge wird die Synthese erzwungen und die Schleife verlassen.
        if isinstance(action, str) and action != "done" and action not in TOOLS:
            unknown_tool_streak += 1
            if unknown_tool_streak >= 2:
                yield {"type": "action", "step": step, "name": action, "args": args}
                obs = {"error": f"Unknown tool '{action}' — forcing synthesis after {unknown_tool_streak} unknown-tool attempts"}
                yield {"type": "observation", "step": step, "content": obs}
                final = await _synthesize("Agent-Schleife auf unbekannten Tools") or "Ich konnte keine klare Antwort formulieren."
                yield {"type": "done", "step": step, "content": final}
                return
        else:
            unknown_tool_streak = 0

        if action == "done":
            final = _to_text(parsed.get("final_answer")) or _to_text(thought)
            if not final:
                final = await _synthesize("done sans final_answer")
            if not final:
                final = "Fertig, aber ich konnte keine klare Antwort formulieren."
            yield {"type": "done", "step": step, "content": final}
            return

        yield {"type": "action", "step": step, "name": action, "args": args}

        tool = TOOLS.get(action)
        if tool is None:
            observation = {"error": f"Unknown tool '{action}'. Available: {list(TOOLS.keys())}"}
        else:
            try:
                observation = await tool.handler(args, db)
            except Exception as e:
                logger.exception("Tool %s failed", action)
                observation = {"error": f"Tool crashed: {e}"}

        yield {"type": "observation", "step": step, "content": observation}
        trace_lines.append(
            f"Assistant: {json.dumps(parsed, ensure_ascii=False)}\n"
            f"Observation: {json.dumps(observation, ensure_ascii=False)[:2000]}"
        )

    # MAX_ITER atteint sans que l'agent ait dit "done" : synthèse forcée
    # basée sur les observations accumulées (mieux qu'un message d'erreur nu).
    final = await _synthesize(f"max iter {MAX_ITERATIONS} atteint") if len(trace_lines) > 1 else ""
    if not final:
        final = f"Maximale Anzahl Iterationen erreicht ({MAX_ITERATIONS})."
    yield {
        "type": "done",
        "step": MAX_ITERATIONS,
        "content": final,
    }


# ─── HTTP endpoint (SSE streaming) ───────────────────────────
class AgentRunRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Anfrage des Nutzers in natürlicher Sprache")
    timeline_state: dict | None = Field(
        None,
        description="Snapshot der aktuellen Timeline (clips, videoTrackIndex, start, duration). Vom Frontend geschickt für Kontext.",
    )


@router.post("/run")
async def run_agent_stream(req: AgentRunRequest, db: AsyncSession = Depends(get_db)):
    """
    Server-Sent Events stream. Chaque événement = une étape ReAct
    (thought / action / observation / done).
    """
    async def event_gen():
        async for evt in _run_agent(req.prompt, db, req.timeline_state):
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


class AgentChatRequest(BaseModel):
    """Frontend-Kompatibilität: der Chat-Panel schickt `message` an
    /api/agent/chat/stream (statt `prompt` an /run). Dünner Alias auf denselben
    ReAct-Stream, damit die UI ohne weitere Änderungen funktioniert."""
    message: str = Field(..., min_length=1)
    timeline_state: dict | None = None


@router.post("/chat/stream")
async def chat_stream(req: AgentChatRequest, db: AsyncSession = Depends(get_db)):
    """Alias SSE für den Chat-Panel (Body: {message, timeline_state})."""
    async def event_gen():
        async for evt in _run_agent(req.message, db, req.timeline_state):
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/run_sync")
async def run_agent_sync(req: AgentRunRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Version non-streaming pour tests CLI : renvoie la trace complète en une fois."""
    trace = []
    async for evt in _run_agent(req.prompt, db, req.timeline_state):
        trace.append(evt)
    final = next((e["content"] for e in reversed(trace) if e["type"] == "done"), None)
    return {"final_answer": final, "trace": trace, "step_count": len(trace)}


# ─── Compare takes : jugement LLM sur 2-4 scènes ─────────────────────
# Endpoint dédié qui utilise llama3 (rapide, ~4-8s) plutôt que qwen2.5:14b
# pour donner un verdict IA sur quel take est le meilleur. Utilisé par la
# modale "Comparison Mode" côté frontend.

COMPARE_MODEL = "llama3"  # 4.7 GB, ~10-15 tok/s sur Mac mini → verdict en 5-10s


class CompareRequest(BaseModel):
    scene_ids: list[str] = Field(..., min_length=2, max_length=4)
    criteria: str | None = Field(
        None,
        description="Kriterium für die Auswahl (z. B. 'bester Emotion', 'schärfstes Bild'). Default = Gesamtqualität für Rohschnitt.",
    )


@router.post("/compare")
async def compare_takes(req: CompareRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Charge N scènes, les compare via llama3, retourne un verdict structuré."""
    from backend.core.database import Szene, Speaker, SceneSpeaker
    from sqlalchemy import select as _sel

    # Charge les scènes + clip parent + speakers
    scenes_rows = (
        await db.execute(
            _sel(Szene)
            .options(selectinload(Szene.clip))
            .where(Szene.id.in_(req.scene_ids))
        )
    ).scalars().all()

    if len(scenes_rows) < 2:
        raise HTTPException(400, "Mindestens 2 gültige Szenen erforderlich.")

    # Résout les speakers par scène
    scene_speakers: dict[str, list[str]] = {}
    for s in scenes_rows:
        ss_rows = (
            await db.execute(
                _sel(SceneSpeaker, Speaker.label_auto)
                .join(Speaker, Speaker.id == SceneSpeaker.speaker_id)
                .where(SceneSpeaker.scene_id == s.id)
            )
        ).all()
        scene_speakers[str(s.id)] = [row[1] for row in ss_rows] if ss_rows else []

    # Sérialize pour la réponse frontend
    scenes_out = []
    for s in scenes_rows:
        thumb = s.thumbnail_pfad
        if thumb and "/temp/" in thumb:
            thumb = thumb[thumb.index("/temp/"):]
        scenes_out.append({
            "scene_id": str(s.id),
            "clip_id": str(s.clip_id),
            "clip_name": s.clip.dateiname if s.clip else "?",
            "clip_proxy_url": f"/proxies/{s.clip_id}_proxy.mp4" if s.clip else None,
            "szenen_nr": s.szenen_nr,
            "start_zeit": s.start_zeit,
            "end_zeit": s.end_zeit,
            "dauer": s.dauer,
            "framing": s.framing,
            "face_count": s.face_count,
            "transkription": s.transkription,
            "beschreibung": s.beschreibung,
            "speakers": scene_speakers.get(str(s.id), []),
            "thumbnail_pfad": thumb,
        })

    # Build prompt structuré pour llama3
    criteria_line = req.criteria or "Gesamtqualität für einen Rohschnitt (klares Framing, verständlicher Dialog, Emotion)"
    takes_txt = ""
    for i, sc in enumerate(scenes_out, start=1):
        speakers_str = ", ".join(sc["speakers"]) if sc["speakers"] else "keine Erkennung"
        takes_txt += (
            f"\nTake {i} — scene_id: {sc['scene_id']}\n"
            f"  Clip: {sc['clip_name']}, Szene {sc['szenen_nr']}\n"
            f"  Zeit: {sc['start_zeit']:.1f}s → {sc['end_zeit']:.1f}s ({sc['dauer']:.1f}s)\n"
            f"  Framing: {sc['framing'] or 'unbekannt'} · Gesichter: {sc['face_count'] or 0}\n"
            f"  Sprecher: {speakers_str}\n"
            f"  Transkription: \"{(sc['transkription'] or '')[:200]}\"\n"
            f"  Beschreibung: {(sc['beschreibung'] or '')[:200]}\n"
        )

    prompt = (
        "Du bist ein erfahrener Videoschnittassistent. Vergleiche diese Takes und wähle "
        f"den besten nach folgendem Kriterium:\n{criteria_line}\n"
        f"{takes_txt}\n"
        "Antworte ausschließlich im JSON-Format:\n"
        '{\n'
        '  "best_scene_id": "<uuid des besten Takes>",\n'
        '  "reasoning": "<1-2 Sätze warum dieser Take am besten ist>",\n'
        '  "per_scene": {\n'
        '    "<scene_id>": {"rank": 1, "note": "<1 Satz zu diesem Take>"},\n'
        '    ...\n'
        "  }\n"
        "}\n"
    )

    # Call llama3 (pas qwen — plus rapide)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": COMPARE_MODEL, "prompt": prompt, "format": "json", "stream": False, "options": {"temperature": 0.3}},
        )
        r.raise_for_status()
        data = r.json()
    wall = time.time() - t0
    resp_text = data.get("response", "")

    try:
        verdict = json.loads(resp_text)
    except Exception:
        verdict = {"best_scene_id": scenes_out[0]["scene_id"], "reasoning": f"JSON-Parsing-Fehler; erster Take als Fallback. Raw: {resp_text[:200]}", "per_scene": {}}

    # Assure que best_scene_id existe dans nos scènes
    valid_ids = {sc["scene_id"] for sc in scenes_out}
    if verdict.get("best_scene_id") not in valid_ids:
        verdict["best_scene_id"] = scenes_out[0]["scene_id"]

    return {
        "scenes": scenes_out,
        "verdict": verdict,
        "meta": {
            "model": COMPARE_MODEL,
            "wall_s": round(wall, 2),
            "tokens": data.get("eval_count", 0),
        },
    }


# ─── Agent proactif : suggestions après ingest ────────────────────────
# À la fin de l'ingest d'un clip (status → "analysiert"), le frontend appelle
# cet endpoint pour recevoir 2-4 suggestions concrètes basées sur ce qu'on a
# détecté (speakers, framings, transkriptions). Chaque suggestion contient un
# `prompt` prêt-à-envoyer à l'agent ReAct normal.
#
# Règles déterministes (pas de LLM) : rapide, prédictible, tune-able.

_HESITATION_PATTERN = re.compile(r"\b(äh+m?|ähm|hmm+|euh+|uhh+|umm+|erm|eh)\b", re.IGNORECASE)


@router.post("/proactive/{clip_id}")
async def proactive_suggestions(clip_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Analyse les données d'ingest d'un clip et propose 2-4 actions concrètes.

    Retourne toujours un objet stable même si le clip n'a pas assez de data
    (ex: pas de scènes) — le frontend affichera juste "keine Vorschläge".
    """
    from backend.core.database import Clip, Szene, Speaker
    from sqlalchemy import select, func

    # Charge clip + scènes en une seule requête
    clip_row = (
        await db.execute(select(Clip).where(Clip.id == clip_id))
    ).scalar_one_or_none()
    if not clip_row:
        raise HTTPException(404, f"Clip {clip_id} nicht gefunden.")

    scenes = (
        await db.execute(
            select(Szene)
            .where(Szene.clip_id == clip_id)
            .order_by(Szene.szenen_nr)
        )
    ).scalars().all()

    n_speakers = (
        await db.execute(
            select(func.count(Speaker.id)).where(Speaker.clip_id == clip_id)
        )
    ).scalar() or 0

    n_scenes = len(scenes)
    n_dialog = sum(1 for s in scenes if (s.transkription or "").strip())
    n_mute = n_scenes - n_dialog
    n_closeup = sum(1 for s in scenes if s.framing in ("closeup", "extreme_closeup"))
    n_wide = sum(1 for s in scenes if s.framing in ("wide_with_person", "wide_no_person"))
    n_medium = sum(1 for s in scenes if s.framing == "medium")

    # Compte les hésitations dans toutes les transkriptions
    n_hesitations = 0
    for s in scenes:
        if s.transkription:
            n_hesitations += len(_HESITATION_PATTERN.findall(s.transkription))

    total_dauer = sum((s.dauer or 0) for s in scenes)
    clip_label = clip_row.dateiname or "Clip"

    suggestions: list[dict] = []

    # Règle #1 : multi-speaker → grouper
    if n_speakers >= 2:
        suggestions.append({
            "title": "Sprecher trennen",
            "description": f"{n_speakers} verschiedene Sprecher erkannt in {clip_label}.",
            "prompt": f"Zeige mir die Szenen mit den {n_speakers} verschiedenen Sprechern in {clip_label} getrennt.",
            "priority": 90,
            "icon": "users",
        })

    # Règle #2 : hésitations → cleanup
    if n_hesitations >= 3:
        suggestions.append({
            "title": "Zögerungen entfernen",
            "description": f"{n_hesitations} Zögerungen (äh, ähm, hmm) im Transkript gefunden.",
            "prompt": f"Entferne die Zögerungen im Clip {clip_label}.",
            "priority": 85,
            "icon": "scissors",
        })

    # Règle #3 : scènes muettes = probable stille → cleanup
    if n_mute >= 2 and n_dialog >= 1:
        suggestions.append({
            "title": "Stille entfernen",
            "description": f"{n_mute} Szenen ohne Dialog erkannt — vermutlich lange Stille zwischen den Sprech-Segmenten.",
            "prompt": f"Entferne die Stille im Clip {clip_label}.",
            "priority": 80,
            "icon": "volume-off",
        })

    # Règle #4 : mix framings → rough cut
    if n_closeup >= 2 and n_wide >= 1:
        suggestions.append({
            "title": "Rohschnitt bauen",
            "description": f"{n_closeup} Close-ups + {n_wide} Weitwinkel + {n_medium} Medium — reicht für einen ersten Rohschnitt.",
            "prompt": f"Baue einen Rohschnitt aus den besten Szenen von {clip_label}.",
            "priority": 75,
            "icon": "film",
        })

    # Règle #5 : clip long → best takes
    if n_scenes >= 8:
        suggestions.append({
            "title": "Beste Takes auswählen",
            "description": f"{n_scenes} Szenen erkannt ({int(total_dauer)}s Material) — bewerten und filtern?",
            "prompt": f"Wähle die 5 besten Szenen aus {clip_label} basierend auf Framing, Sprecher und Dialog.",
            "priority": 70,
            "icon": "star",
        })

    # Règle #6 (fallback) : au moins proposer un splitten sur les scènes
    if not suggestions and n_scenes >= 3:
        suggestions.append({
            "title": "Szenen splitten",
            "description": f"{n_scenes} Szenen erkannt — auf die Timeline geteilt bringen?",
            "prompt": f"Teile den Clip {clip_label} an den erkannten Szenengrenzen.",
            "priority": 50,
            "icon": "scissors",
        })

    suggestions.sort(key=lambda s: -s["priority"])

    return {
        "clip_id": clip_id,
        "clip_name": clip_label,
        "stats": {
            "n_scenes": n_scenes,
            "n_speakers": n_speakers,
            "n_dialog_scenes": n_dialog,
            "n_mute_scenes": n_mute,
            "n_closeup": n_closeup,
            "n_medium": n_medium,
            "n_wide": n_wide,
            "n_hesitations": n_hesitations,
            "total_dauer": round(total_dauer, 1),
        },
        "suggestions": suggestions[:4],  # max 4 pour ne pas noyer l'UI
    }


@router.get("/tools")
async def list_tools() -> dict:
    """Liste des tools disponibles à l'agent (pour debug / doc)."""
    return {
        "model": AGENT_MODEL,
        "max_iterations": MAX_ITERATIONS,
        "tools": [
            {"name": t.name, "description": t.description, "args_schema": t.args_schema}
            for t in TOOLS.values()
        ],
    }
