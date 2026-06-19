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

from backend.core.database import get_db, Timeline

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
