"""
CinAssist — Medien-Ordner (Bins) im Medien-Panel

GET    /api/ordner                       → alle Ordner (flach, mit eltern_id + Clip-Anzahl)
POST   /api/ordner                       → Ordner anlegen {name, eltern_id?}
PATCH  /api/ordner/{id}                  → umbenennen / verschieben {name?, eltern_id?}
DELETE /api/ordner/{id}                  → löschen (Clips wandern in den Elternordner, Unterordner werden gelöscht)
POST   /api/ordner/verschieben           → Clips in einen Ordner verschieben {clip_ids, ordner_id|null}
POST   /api/ordner/importieren           → Video-Ordner per Referenz importieren → Ordner + Clips + Analyse (Celery)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db, Clip, Job, MedienOrdner, OrdnerImport

router = APIRouter(prefix="/api/ordner", tags=["Medien-Ordner"])


class OrdnerAnlegen(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    eltern_id: Optional[str] = None


class OrdnerAendern(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    eltern_id: Optional[str] = None
    eltern_loesen: bool = False        # True → in die Wurzel verschieben


class ClipsVerschieben(BaseModel):
    clip_ids: list[str]
    ordner_id: Optional[str] = None    # None = Wurzel


class OrdnerImportieren(BaseModel):
    pfad: str
    eltern_id: Optional[str] = None
    analyse_starten: bool = True
    quelle: str = Field("A", pattern="^[AB]$")


def _uuid(v: Optional[str], was: str = "ID") -> Optional[uuid.UUID]:
    if v is None:
        return None
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(400, f"Ungültige {was}")


def _dict(o: MedienOrdner, anzahl: int = 0) -> dict:
    return {"id": str(o.id), "name": o.name, "eltern_id": str(o.eltern_id) if o.eltern_id else None,
            "quelle_pfad": o.quelle_pfad, "anzahl_clips": anzahl,
            "erstellt_am": o.erstellt_am.isoformat() if o.erstellt_am else None}


@router.get("")
async def ordner_auflisten(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MedienOrdner).order_by(MedienOrdner.name))).scalars().all()
    zaehler = dict((await db.execute(select(Clip.ordner_id, func.count(Clip.id)).group_by(Clip.ordner_id))).all())
    return [_dict(o, int(zaehler.get(o.id, 0))) for o in rows] + [
        {"id": None, "name": "Wurzel", "eltern_id": None, "quelle_pfad": None, "anzahl_clips": int(zaehler.get(None, 0)), "erstellt_am": None}
    ]


@router.post("")
async def ordner_anlegen(anfrage: OrdnerAnlegen, db: AsyncSession = Depends(get_db)):
    eid = _uuid(anfrage.eltern_id, "Eltern-ID")
    if eid and not (await db.execute(select(MedienOrdner).where(MedienOrdner.id == eid))).scalar_one_or_none():
        raise HTTPException(404, "Elternordner nicht gefunden")
    o = MedienOrdner(id=uuid.uuid4(), name=anfrage.name.strip(), eltern_id=eid)
    db.add(o)
    await db.commit()
    return _dict(o)


@router.patch("/{ordner_id}")
async def ordner_aendern(ordner_id: str, anfrage: OrdnerAendern, db: AsyncSession = Depends(get_db)):
    o = (await db.execute(select(MedienOrdner).where(MedienOrdner.id == _uuid(ordner_id)))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Ordner nicht gefunden")
    if anfrage.name:
        o.name = anfrage.name.strip()
    if anfrage.eltern_loesen:
        o.eltern_id = None
    elif anfrage.eltern_id is not None:
        eid = _uuid(anfrage.eltern_id, "Eltern-ID")
        if eid == o.id:
            raise HTTPException(409, "Ordner kann nicht in sich selbst verschoben werden")
        # Zyklen verhindern: neuer Elternordner darf kein Nachfahre sein.
        cur = eid
        while cur:
            p = (await db.execute(select(MedienOrdner).where(MedienOrdner.id == cur))).scalar_one_or_none()
            if not p:
                raise HTTPException(404, "Elternordner nicht gefunden")
            if p.eltern_id == o.id:
                raise HTTPException(409, "Zyklus: Zielordner liegt unterhalb dieses Ordners")
            cur = p.eltern_id
        o.eltern_id = eid
    await db.commit()
    return _dict(o)


@router.delete("/{ordner_id}")
async def ordner_loeschen(ordner_id: str, db: AsyncSession = Depends(get_db)):
    o = (await db.execute(select(MedienOrdner).where(MedienOrdner.id == _uuid(ordner_id)))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Ordner nicht gefunden")
    # Clips (auch aus Unterordnern) in den Elternordner retten — Clips werden nie gelöscht.
    ids = [o.id]
    front = [o.id]
    while front:
        kinder = (await db.execute(select(MedienOrdner.id).where(MedienOrdner.eltern_id.in_(front)))).scalars().all()
        ids += kinder
        front = kinder
    await db.execute(update(Clip).where(Clip.ordner_id.in_(ids)).values(ordner_id=o.eltern_id))
    await db.delete(o)
    await db.commit()
    return {"geloescht": True, "clips_verschoben_nach": str(o.eltern_id) if o.eltern_id else None}


@router.post("/verschieben")
async def clips_verschieben(anfrage: ClipsVerschieben, db: AsyncSession = Depends(get_db)):
    ziel = _uuid(anfrage.ordner_id, "Ordner-ID")
    if ziel and not (await db.execute(select(MedienOrdner).where(MedienOrdner.id == ziel))).scalar_one_or_none():
        raise HTTPException(404, "Zielordner nicht gefunden")
    ids = [_uuid(c, "Clip-ID") for c in anfrage.clip_ids]
    r = await db.execute(update(Clip).where(Clip.id.in_(ids)).values(ordner_id=ziel))
    await db.commit()
    return {"verschoben": r.rowcount, "ordner_id": str(ziel) if ziel else None}


@router.post("/importieren")
async def ordner_importieren(anfrage: OrdnerImportieren, db: AsyncSession = Depends(get_db)):
    """Video-Ordner per Referenz importieren: Medien-Ordner mit dem Ordnernamen anlegen, Dateien
    scannen (Celery), pro Video einen Clip erzeugen (keine Kopie) und — optional — die Analyse starten.
    Ton-Ordner gehören in den Tab „Synchronisation“ (dort werden sie den Videos zugeordnet)."""
    from backend.workers.sync import ordner_import_analyse_task
    pfad = Path(anfrage.pfad).expanduser()
    if not pfad.is_absolute() or not pfad.is_dir():
        raise HTTPException(404, f"Ordner nicht gefunden: {pfad}")
    eid = _uuid(anfrage.eltern_id, "Eltern-ID")
    o = (await db.execute(select(MedienOrdner).where(MedienOrdner.quelle_pfad == str(pfad)))).scalars().first()
    if o is None:
        o = MedienOrdner(id=uuid.uuid4(), name=pfad.name or str(pfad), eltern_id=eid, quelle_pfad=str(pfad))
        db.add(o)
    imp = (await db.execute(select(OrdnerImport).where(OrdnerImport.pfad == str(pfad), OrdnerImport.typ == "video"))).scalars().first()
    if imp is None:
        imp = OrdnerImport(id=uuid.uuid4(), pfad=str(pfad), typ="video", status="wartend")
        db.add(imp)
    else:
        imp.status = "wartend"
    job = Job(id=uuid.uuid4(), typ="import", status="wartend", fortschritt=0, nachricht=f"Ordner-Import: {pfad.name}")
    db.add(job)
    await db.commit()
    imp.job_id = job.id
    await db.commit()
    task = ordner_import_analyse_task.delay(str(imp.id), str(job.id), str(o.id), anfrage.analyse_starten, anfrage.quelle)
    job.celery_task_id = task.id
    await db.commit()
    return {"ordner": _dict(o), "import_id": str(imp.id), "job_id": str(job.id)}
