"""
CinAssist — Timeline API

POST /api/timelines           → Neue Timeline erstellen
GET  /api/timelines           → Alle Timelines auflisten
GET  /api/timelines/{id}      → Timeline-Details
PUT  /api/timelines/{id}      → Timeline aktualisieren
DELETE /api/timelines/{id}    → Timeline löschen
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db, Timeline, Clip
from backend.core.timeline_generator import (
    assemble_timeline,
    generate_story_from_pool,
    plan_timeline,
    retrieve_candidates,
    summarize_pool,
    _log_stage,
)
import time

router = APIRouter(prefix="/api/timelines", tags=["Timeline"])


# ─── Pydantic Models ────────────────────────────────────

class TimelineCreate(BaseModel):
    name: str = "Unbenannt"
    stil: str | None = None
    prompt: str | None = None
    daten: dict

class TimelineUpdate(BaseModel):
    name: str | None = None
    daten: dict | None = None


# ─── Erstellen ───────────────────────────────────────────

@router.post("")
async def timeline_erstellen(body: TimelineCreate, db: AsyncSession = Depends(get_db)):
    """Neue Timeline erstellen / speichern."""
    tl_id = str(uuid.uuid4())

    # Gesamtdauer berechnen
    segmente = body.daten.get("segmente", [])
    gesamtdauer = 0.0
    for seg in segmente:
        end = seg.get("start", 0) + seg.get("dauer", 0)
        if end > gesamtdauer:
            gesamtdauer = end

    tl = Timeline(
        id=tl_id,
        name=body.name,
        stil=body.stil,
        prompt=body.prompt,
        daten=body.daten,
        gesamtdauer=gesamtdauer,
    )
    db.add(tl)
    await db.commit()

    return _tl_to_dict(tl)


# ─── Alle auflisten ─────────────────────────────────────

@router.get("")
async def timelines_auflisten(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Timeline).order_by(Timeline.erstellt_am.desc()))
    return [_tl_to_dict(tl) for tl in result.scalars().all()]


# ─── Details ─────────────────────────────────────────────

@router.get("/{tl_id}")
async def timeline_details(tl_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Timeline).where(Timeline.id == tl_id))
    tl = result.scalar_one_or_none()
    if not tl:
        raise HTTPException(404, "Timeline nicht gefunden.")
    return _tl_to_dict(tl)


# ─── Aktualisieren ──────────────────────────────────────

@router.put("/{tl_id}")
async def timeline_aktualisieren(tl_id: str, body: TimelineUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Timeline).where(Timeline.id == tl_id))
    tl = result.scalar_one_or_none()
    if not tl:
        raise HTTPException(404, "Timeline nicht gefunden.")

    if body.name is not None:
        tl.name = body.name
    if body.daten is not None:
        tl.daten = body.daten
        segmente = body.daten.get("segmente", [])
        gesamtdauer = 0.0
        for seg in segmente:
            end = seg.get("start", 0) + seg.get("dauer", 0)
            if end > gesamtdauer:
                gesamtdauer = end
        tl.gesamtdauer = gesamtdauer

    await db.commit()
    return _tl_to_dict(tl)


# ─── Löschen ────────────────────────────────────────────

@router.delete("/{tl_id}")
async def timeline_loeschen(tl_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Timeline).where(Timeline.id == tl_id))
    tl = result.scalar_one_or_none()
    if not tl:
        raise HTTPException(404, "Timeline nicht gefunden.")
    await db.delete(tl)
    await db.commit()
    return {"status": "gelöscht"}


# ─── Generierung aus Prompt (Kern der Bachelorarbeit) ────

class TimelineGenerateRequest(BaseModel):
    prompt: str
    duration_s: float = 60.0
    clip_ids: list[str] | None = None      # None → alle "analysiert"-Clips
    num_slots_hint: int | None = None
    top_k: int = 5
    dedupe_across_slots: bool = True
    use_query_rewrite: bool = False        # llama3 reichert die Absichten vor CLIP an
    assemble_mode: str = "heuristic"       # "heuristic" | "llm"
    save_timeline: bool = True             # persist als Timeline-Zeile
    timeline_name: str | None = None


@router.post("/generate")
async def timeline_aus_prompt_generieren(
    body: TimelineGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generiert eine Timeline aus einer natürlichen Beschreibung —
    Kernfunktion der Bachelorarbeit (Plan → Retrieve → Assemble).

    Antwort enthält alle 3 Phasen-Outputs zur Nachvollziehbarkeit + optional
    eine persistierte Timeline-ID.
    """
    run_id = f"api_{int(time.time())}"

    # Clip-Pool ermitteln
    if body.clip_ids:
        r = await db.execute(select(Clip.id).where(Clip.id.in_(body.clip_ids)))
    else:
        r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
    pool_ids = [str(cid) for (cid,) in r.all()]
    if not pool_ids:
        raise HTTPException(400, "Keine analysierten Clips im Pool gefunden.")

    # Phase 0: Zusammenfassung des Bestands für die bestandsbewusste Planung
    pool_summary = await summarize_pool(db, pool_ids)
    _log_stage("00_pool_summary", pool_summary, run_id)

    # Phase 1: Zerlegung der Anfrage, gestützt auf die Bestandszusammenfassung
    plan = await plan_timeline(body.prompt, body.duration_s, body.num_slots_hint,
                               pool_summary=pool_summary)
    _log_stage("01_plan", plan, run_id)

    # Phase 2 : Retrieve (hybride CLIP + BM25)
    candidates = await retrieve_candidates(
        plan, pool_ids, db,
        top_k=body.top_k,
        dedupe_across_slots=body.dedupe_across_slots,
        use_query_rewrite=body.use_query_rewrite,
    )
    _log_stage("02_candidates", candidates, run_id)

    # Phase 3: Zusammenstellung mit Nachfüllen bis zur Zieldauer
    timeline_data = await assemble_timeline(plan, candidates, mode=body.assemble_mode,
                                            target_duration_s=body.duration_s)
    _log_stage("03_timeline", timeline_data, run_id)

    saved_id: str | None = None
    if body.save_timeline and timeline_data["segments"]:
        name = body.timeline_name or f"Prompt: {body.prompt[:60]}"
        payload = {
            "segmente": timeline_data["segments"],
            "gesamtdauer": timeline_data["_meta"]["total_duration_s"],
            "plan": plan,
            "decisions": timeline_data["decisions"],
            "run_id": run_id,
        }
        tl = Timeline(
            name=name,
            stil="prompt_generiert",
            prompt=body.prompt,
            daten=payload,
            gesamtdauer=timeline_data["_meta"]["total_duration_s"],
        )
        db.add(tl)
        await db.commit()
        await db.refresh(tl)
        saved_id = str(tl.id)

    return {
        "run_id": run_id,
        "plan": plan,
        "candidates_summary": {
            sid: [{"scene_id": c["scene_id"], "clip_score": c["clip_score"],
                   "clip_name": c["clip_name"]} for c in cands[:3]]
            for sid, cands in (candidates.get("slots") or {}).items()
        },
        "timeline": timeline_data,
        "saved_timeline_id": saved_id,
        "pool_size_clips": len(pool_ids),
    }


# ─── Generierung « material-first » (erster Rohschnitt / Story) ───

class TimelineStoryRequest(BaseModel):
    clip_ids: list[str] | None = None      # None → alle "analysiert"-Clips
    duration_s: float | None = None        # Zieldauer (optional, ca.)
    save_timeline: bool = True
    timeline_name: str | None = None


@router.post("/generate-story")
async def timeline_story_generieren(
    body: TimelineStoryRequest,
    db: AsyncSession = Depends(get_db),
):
    """Material-first : baut aus dem VORHANDENEN Material die kohärenteste kurze
    Geschichte (kein Prompt nötig). Erfindet nichts, was nicht im Pool ist —
    ideal für einen ersten Rohschnitt."""
    run_id = f"story_{int(time.time())}"
    if body.clip_ids:
        r = await db.execute(select(Clip.id).where(Clip.id.in_(body.clip_ids)))
    else:
        r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
    pool_ids = [str(cid) for (cid,) in r.all()]
    if not pool_ids:
        raise HTTPException(400, "Keine analysierten Clips im Pool gefunden.")

    story = await generate_story_from_pool(db, pool_ids, target_duration_s=body.duration_s)
    _log_stage("story", story, run_id)

    if not story["segments"]:
        raise HTTPException(422, "Keine Story aus dem Material generierbar (leerer Pool?).")

    saved_id: str | None = None
    if body.save_timeline:
        name = body.timeline_name or (story.get("story_title") or "Auto-Rohschnitt")
        payload = {
            "segmente": story["segments"],
            "gesamtdauer": story["_meta"]["total_duration_s"],
            "story_title": story.get("story_title"),
            "narrative_intent_de": story.get("narrative_intent_de"),
            "decisions": story["decisions"],
            "run_id": run_id,
        }
        tl = Timeline(name=name, stil="story_generiert", prompt=None, daten=payload,
                      gesamtdauer=story["_meta"]["total_duration_s"])
        db.add(tl)
        await db.commit()
        await db.refresh(tl)
        saved_id = str(tl.id)

    return {
        "run_id": run_id,
        "story_title": story.get("story_title"),
        "narrative_intent_de": story.get("narrative_intent_de"),
        "timeline": story,
        "saved_timeline_id": saved_id,
        "pool_size_clips": len(pool_ids),
    }


# ─── Helper ─────────────────────────────────────────────

def _tl_to_dict(tl: Timeline) -> dict:
    return {
        "id": str(tl.id),
        "name": tl.name,
        "stil": tl.stil,
        "prompt": tl.prompt,
        "daten": tl.daten,
        "gesamtdauer": tl.gesamtdauer,
        "erstellt_am": tl.erstellt_am.isoformat() if tl.erstellt_am else None,
    }
