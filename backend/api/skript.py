"""API — Drehbuch, Kontext-Schicht, Schnittplan.

  POST /api/skript/upload                 Datei (PDF/TXT/Fountain) → Import-Job (parse + Übersetzung)
  GET  /api/skript                        aktives Skript mit Szenen + Zeilen (+ Kontext-Status)
  PUT  /api/skript/zeile/{id}             Zeile (text_ziel) manuell korrigieren
  POST /api/skript/kontext/aufbauen       Job: L2→L3→L4
  GET  /api/skript/kontext                Szenen-Kontexte + Story + Take-Kontexte (kompakt)
  GET  /api/skript/kontext/take/{clip_id} Take-Kontext im Detail
  PUT  /api/skript/kontext/take/{clip_id} Klappe/Bewertung manuell setzen (gewinnt gegen Automatik)
  POST /api/skript/schnittplan            Job: Rohschnitt erzeugen
  GET  /api/skript/schnittplan            Liste · GET /api/skript/schnittplan/{id} Einträge inkl. Clip-URLs fürs Laden in die Timeline
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import DATA_DIR
from backend.core.database import (get_db, Clip, Job, Skript, SkriptSzene, SkriptZeile, TakeKontext, SzenenKontext,
                                   StoryKontext, Schnittplan)
from backend.core.medien import clip_video_url, proxy_dateiname  # noqa: F401

router = APIRouter(prefix="/api/skript", tags=["skript"])
SKRIPT_DIR = DATA_DIR / "skripte"
SKRIPT_DIR.mkdir(parents=True, exist_ok=True)


def _job(db, typ: str, nachricht: str) -> Job:
    return Job(id=uuid.uuid4(), typ=typ, clip_id=None, status="wartend", fortschritt=0, nachricht=nachricht)


@router.post("/upload")
async def skript_hochladen(datei: UploadFile = File(...), ziel_sprache: str = Form("de"), db: AsyncSession = Depends(get_db)):
    from backend.workers.kontext import skript_import_task
    name = Path(datei.filename or "skript").name
    if not name.lower().endswith((".pdf", ".txt", ".fountain", ".md")):
        raise HTTPException(400, "Erlaubt: PDF, TXT, Fountain")
    ziel = SKRIPT_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
    with ziel.open("wb") as f:
        shutil.copyfileobj(datei.file, f)
    # Die Sprache des Drehs bestimmt zweierlei: in welche Sprache das Drehbuch
    # übersetzt wird und in welcher Sprache Whisper die Aufnahmen liest. Bislang
    # war nur das Erste damit verbunden. Stand die Transkription auf einer anderen
    # Sprache, entstanden unbrauchbare Transkripte, ohne dass es jemand bemerkte.
    from backend.core import einstellungen as _E
    vorher = (_E.transkription().get("sprache") or "").lower()
    angepasst = False
    if ziel_sprache and ziel_sprache != "auto" and vorher != ziel_sprache:
        _E.speichere({"transkription": {**_E.transkription(), "sprache": ziel_sprache}})
        angepasst = True

    job = _job(db, "skript_import", "Drehbuch-Import wartet…")
    db.add(job); await db.commit()
    t = skript_import_task.delay(str(ziel), name, ziel_sprache, str(job.id))
    job.celery_task_id = t.id; await db.commit()
    return {
        "job_id": str(job.id),
        "transkription_sprache": ziel_sprache,
        "transkription_angepasst": angepasst,
        "transkription_vorher": vorher or None,
    }


@router.get("/job/{job_id}")
async def job_status(job_id: str, db: AsyncSession = Depends(get_db)):
    j = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if not j:
        raise HTTPException(404, "Job nicht gefunden.")
    return {"id": str(j.id), "typ": j.typ, "status": j.status, "fortschritt": j.fortschritt, "nachricht": j.nachricht,
            "ergebnis": j.ergebnis, "aktualisiert_am": j.aktualisiert_am.isoformat() if j.aktualisiert_am else None}


def _skript_json(sk: Skript, ctx_by_szene: dict, takes_by_szene: dict) -> dict:
    return {
        "id": str(sk.id), "name": sk.name, "titel": sk.titel, "sprache": sk.sprache, "ziel_sprache": sk.ziel_sprache,
        "status": sk.status, "erstellt_am": sk.erstellt_am.isoformat() if sk.erstellt_am else None,
        "szenen": [
            {
                "id": str(sz.id), "nummer": sz.nummer, "reihenfolge": sz.reihenfolge, "ueberschrift": sz.ueberschrift,
                "innen_aussen": sz.innen_aussen, "ort": sz.ort, "tageszeit": sz.tageszeit, "figuren": sz.figuren or [],
                "zeilen": [{"id": str(z.id), "nr": z.nr, "art": z.art, "figur": z.figur, "regie": z.regie, "text": z.text,
                            "text_ziel": z.text_ziel, "text_ziel_quelle": z.text_ziel_quelle} for z in sz.zeilen],
                "takes": takes_by_szene.get(sz.id, 0),
                "kontext": ctx_by_szene.get(sz.id),
            }
            for sz in sk.szenen
        ],
    }


def _ctx_json(c: SzenenKontext | None) -> dict | None:
    if c is None:
        return None
    return {"zusammenfassung": c.zusammenfassung, "beats": c.beats, "figuren": c.figuren, "coverage": c.coverage,
            "take_ranking": c.take_ranking, "belege": c.belege, "unsicher": c.unsicher, "manuell_geprueft": c.manuell_geprueft,
            "aktions_coverage": c.aktions_coverage,
            "aktualisiert_am": c.aktualisiert_am.isoformat() if c.aktualisiert_am else None}


@router.get("")
async def skript_lesen(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc())
                         .options(selectinload(Skript.szenen).selectinload(SkriptSzene.zeilen)))
    sk = r.scalars().first()
    if not sk:
        return {"skript": None}
    ctxs = {c.skript_szene_id: _ctx_json(c) for c in (await db.execute(select(SzenenKontext))).scalars().all()}
    tk_rows = (await db.execute(select(TakeKontext.skript_szene_id))).all()
    takes_by: dict = {}
    for (sid,) in tk_rows:
        if sid:
            takes_by[sid] = takes_by.get(sid, 0) + 1
    story = (await db.execute(select(StoryKontext).where(StoryKontext.skript_id == sk.id))).scalar_one_or_none()
    return {"skript": _skript_json(sk, ctxs, takes_by),
            "story": ({"zusammenfassung": story.zusammenfassung, "figuren": story.figuren, "szenenfolge": story.szenenfolge,
                       "arc": story.arc, "motive": story.motive, "unsicher": story.unsicher,
                       "aktualisiert_am": story.aktualisiert_am.isoformat() if story.aktualisiert_am else None} if story else None)}


class ZeileUpdate(BaseModel):
    text_ziel: Optional[str] = None
    text: Optional[str] = None
    figur: Optional[str] = None


@router.put("/zeile/{zeile_id}")
async def zeile_aendern(zeile_id: str, body: ZeileUpdate, db: AsyncSession = Depends(get_db)):
    z = (await db.execute(select(SkriptZeile).where(SkriptZeile.id == zeile_id))).scalar_one_or_none()
    if not z:
        raise HTTPException(404, "Zeile nicht gefunden.")
    if body.text_ziel is not None:
        z.text_ziel = body.text_ziel; z.text_ziel_quelle = "manuell"
    if body.text is not None:
        z.text = body.text
    if body.figur is not None:
        z.figur = body.figur
    await db.commit()
    return {"ok": True}


@router.post("/kontext/aufbauen")
async def kontext_aufbauen(mit_llm: bool = True, db: AsyncSession = Depends(get_db)):
    from backend.workers.kontext import kontext_aufbauen_task
    sk = (await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()))).scalars().first()
    if not sk:
        raise HTTPException(400, "Kein aktives Drehbuch — zuerst hochladen.")
    job = _job(db, "kontext", "Kontext-Aufbau wartet…")
    db.add(job); await db.commit()
    t = kontext_aufbauen_task.delay(str(sk.id), str(job.id), mit_llm)
    job.celery_task_id = t.id; await db.commit()
    return {"job_id": str(job.id)}


@router.post("/kontext/aktionen")
async def aktionen_pruefen(szenen: Optional[str] = None, neu_fragen: bool = False, db: AsyncSession = Depends(get_db)):
    """Skript-gesteuerte Bildprüfung (VQA) — optional nur bestimmte Szenen (Komma-Liste „2,4“)."""
    from backend.workers.kontext import aktionen_pruefen_task
    sk = (await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()))).scalars().first()
    if not sk:
        raise HTTPException(400, "Kein aktives Drehbuch.")
    job = _job(db, "aktionen", "Bildprüfung wartet…")
    db.add(job); await db.commit()
    liste = [x.strip() for x in szenen.split(",") if x.strip()] if szenen else None
    t = aktionen_pruefen_task.delay(str(sk.id), str(job.id), liste, neu_fragen)
    job.celery_task_id = t.id; await db.commit()
    return {"job_id": str(job.id)}


@router.post("/gesichter/erkennen")
async def gesichter_erkennen(db: AsyncSession = Depends(get_db)):
    from backend.workers.kontext import gesichter_task
    sk = (await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()))).scalars().first()
    if not sk:
        raise HTTPException(400, "Kein aktives Drehbuch.")
    job = _job(db, "gesichter", "Gesichtserkennung wartet…")
    db.add(job); await db.commit()
    t = gesichter_task.delay(str(sk.id), str(job.id))
    job.celery_task_id = t.id; await db.commit()
    return {"job_id": str(job.id)}


@router.get("/gesichter")
async def gesichter_liste(db: AsyncSession = Depends(get_db)):
    from backend.core.database import GesichtsCluster
    rows = (await db.execute(select(GesichtsCluster).order_by(GesichtsCluster.anzahl.desc()))).scalars().all()
    return [{"id": str(g.id), "idx": g.idx, "anzahl": g.anzahl, "takes": g.takes, "name_skript": g.name_skript, "name_film": g.name_film,
             "score": g.score, "manuell": g.manuell, "thumb_url": g.thumb_pfad, "szenen_anteil": g.szenen_anteil} for g in rows]


class GesichtUpdate(BaseModel):
    name_skript: Optional[str] = None
    name_film: Optional[str] = None


@router.put("/gesichter/{gid}")
async def gesicht_benennen(gid: str, body: GesichtUpdate, db: AsyncSession = Depends(get_db)):
    from backend.core.database import GesichtsCluster
    g = (await db.execute(select(GesichtsCluster).where(GesichtsCluster.id == gid))).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Person nicht gefunden.")
    if body.name_skript is not None:
        g.name_skript = body.name_skript.strip().upper() or None
    if body.name_film is not None:
        g.name_film = body.name_film.strip() or None
    g.manuell = True
    await db.commit()
    return {"ok": True}


@router.post("/kontext/ordner-sortieren")
async def ordner_nach_skript_sortieren(db: AsyncSession = Depends(get_db)):
    """Medien-Ordner nach Skript-Szene: legt (falls nötig) Ordner „Szene N“ an und verschiebt jeden Clip in den Ordner
    seiner Skript-Szene (aus Klappe/Alignment) — damit „Ordner = Szene“ auch stimmt, wenn die Kamera-Nummerierung abweicht."""
    from backend.core.database import MedienOrdner
    sk = (await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc())
                           .options(selectinload(Skript.szenen)))).scalars().first()
    if not sk:
        raise HTTPException(400, "Kein aktives Drehbuch.")
    tks = (await db.execute(select(TakeKontext))).scalars().all()
    ordner = {o.name: o for o in (await db.execute(select(MedienOrdner).where(MedienOrdner.eltern_id.is_(None)))).scalars().all()}
    verschoben = 0
    angelegt: list[str] = []
    ohne_material: list[str] = []
    for sz in sk.szenen:
        # Erst prüfen, ob dieser Szene überhaupt Aufnahmen zugeordnet sind. Ein Ordner
        # für eine Szene, für die nichts gedreht wurde, ist im Medien-Fenster nur
        # Rauschen; dass sie fehlt, sagt der Schnittplan als Lücke ohnehin.
        clips_der_szene = []
        for tk in tks:
            if tk.skript_szene_id == sz.id:
                c = (await db.execute(select(Clip).where(Clip.id == tk.clip_id))).scalar_one_or_none()
                if c:
                    clips_der_szene.append(c)
        name = f"Szene {sz.nummer}"
        if not clips_der_szene:
            ohne_material.append(name)
            continue
        o = ordner.get(name)
        if o is None:
            o = MedienOrdner(id=uuid.uuid4(), name=name, eltern_id=None)
            db.add(o); await db.flush(); ordner[name] = o
        angelegt.append(name)
        for c in clips_der_szene:
            if c.ordner_id != o.id:
                c.ordner_id = o.id; verschoben += 1
    await db.commit()
    return {"verschoben": verschoben, "ordner": angelegt, "ohne_material": ohne_material}


def _tk_json(tk: TakeKontext, clip: Clip | None, kompakt: bool = False) -> dict:
    d = {"clip_id": str(tk.clip_id), "dateiname": clip.dateiname if clip else None, "dauer": clip.dauer if clip else None,
         "skript_szene_id": str(tk.skript_szene_id) if tk.skript_szene_id else None,
         "slate_szene": tk.slate_szene, "slate_take": tk.slate_take, "slate_quelle": tk.slate_quelle, "slate_konflikt": tk.slate_konflikt,
         "einstellung": tk.einstellung, "spiel_start_s": tk.spiel_start_s, "spiel_ende_s": tk.spiel_ende_s, "ng": tk.ng,
         "abdeckung": tk.abdeckung, "bewertung": tk.bewertung, "notiz": tk.notiz, "aktionen": tk.aktionen, "gesichter": tk.gesichter}
    if not kompakt:
        d["zeilen"] = tk.zeilen; d["bildverlauf"] = tk.bildverlauf
    return d


@router.get("/kontext")
async def kontext_lesen(db: AsyncSession = Depends(get_db)):
    tks = (await db.execute(select(TakeKontext))).scalars().all()
    clips = {c.id: c for c in (await db.execute(select(Clip))).scalars().all()}
    return {"takes": [_tk_json(t, clips.get(t.clip_id), kompakt=True) for t in tks]}


@router.get("/kontext/take/{clip_id}")
async def take_kontext_lesen(clip_id: str, db: AsyncSession = Depends(get_db)):
    tk = (await db.execute(select(TakeKontext).where(TakeKontext.clip_id == clip_id))).scalar_one_or_none()
    if not tk:
        raise HTTPException(404, "Kein Take-Kontext — Kontext zuerst aufbauen.")
    clip = (await db.execute(select(Clip).where(Clip.id == clip_id))).scalar_one_or_none()
    return _tk_json(tk, clip)


class TakeUpdate(BaseModel):
    slate_szene: Optional[str] = None       # "5.2.1" → Skript-Szene 5, Einstellung 5.2.1
    slate_take: Optional[int] = None
    bewertung: Optional[str] = None         # circled | ok | ng | null
    notiz: Optional[str] = None


@router.put("/kontext/take/{clip_id}")
async def take_kontext_setzen(clip_id: str, body: TakeUpdate, db: AsyncSession = Depends(get_db)):
    tk = (await db.execute(select(TakeKontext).where(TakeKontext.clip_id == clip_id))).scalar_one_or_none()
    if not tk:
        raise HTTPException(404, "Kein Take-Kontext.")
    if body.slate_szene is not None:
        tk.slate_szene = body.slate_szene.strip() or None
        tk.einstellung = tk.slate_szene
        tk.slate_quelle = "manuell"
        tk.slate_konflikt = False
        nr = (tk.slate_szene or "").split(".")[0]
        sk = (await db.execute(select(Skript).where(Skript.aktiv.is_(True)))).scalars().first()
        sz = (await db.execute(select(SkriptSzene).where(SkriptSzene.skript_id == sk.id, SkriptSzene.nummer == nr))).scalar_one_or_none() if sk and nr else None
        tk.skript_szene_id = sz.id if sz else None
    if body.slate_take is not None:
        tk.slate_take = body.slate_take
    if body.bewertung is not None:
        tk.bewertung = body.bewertung or None
    if body.notiz is not None:
        tk.notiz = body.notiz
    await db.commit()
    return {"ok": True, "hinweis": "Zuordnung gespeichert — „Kontext aufbauen“ aktualisiert Alignment/Ranking, „Rohschnitt erzeugen“ nutzt sie sofort."}


class PlanAnfrage(BaseModel):
    name: Optional[str] = None
    modus: str = "rohschnitt"            # rohschnitt | feinschnitt
    coverage_wechsel: bool = True
    stumm_max_s: float = 75.0
    insert_dauer_s: float = 3.5
    beats: bool = True                   # Schnitt nach Beats (Szenen-Takt); False = alter Zeilen-Modus (Vergleich)
    takt_neu: bool = False               # Beat-Segmentierung aller Takes neu rechnen


@router.post("/schnittplan")
async def schnittplan_erzeugen(body: PlanAnfrage, db: AsyncSession = Depends(get_db)):
    from backend.workers.kontext import schnittplan_task
    sk = (await db.execute(select(Skript).where(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()))).scalars().first()
    if not sk:
        raise HTTPException(400, "Kein aktives Drehbuch.")
    job = _job(db, "schnittplan", "Rohschnitt wartet…")
    db.add(job); await db.commit()
    if body.modus not in ("rohschnitt", "feinschnitt"):
        raise HTTPException(400, "modus: rohschnitt | feinschnitt")
    t = schnittplan_task.delay(str(sk.id), str(job.id), body.name,
                               {"modus": body.modus, "coverage_wechsel": body.coverage_wechsel, "stumm_max_s": body.stumm_max_s, "insert_dauer_s": body.insert_dauer_s,
                                "beats": body.beats, "takt_neu": body.takt_neu})
    job.celery_task_id = t.id; await db.commit()
    return {"job_id": str(job.id)}


@router.get("/schnittplan")
async def schnittplaene(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Schnittplan).order_by(Schnittplan.erstellt_am.desc()))).scalars().all()
    return [{"id": str(p.id), "name": p.name, "erstellt_am": p.erstellt_am.isoformat() if p.erstellt_am else None,
             "statistik": p.statistik, "parameter": p.parameter} for p in rows]


@router.get("/schnittplan/{plan_id}")
async def schnittplan_lesen(plan_id: str, db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(Schnittplan).where(Schnittplan.id == plan_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Schnittplan nicht gefunden.")
    # Clip-Infos fürs Laden in die Timeline (gleiche Felder wie /api/clips)
    from backend.api.clips import _clip_medien_felder  # type: ignore[attr-defined]
    ids = {e["clip_id"] for e in (p.eintraege or [])}
    clips = {str(c.id): c for c in (await db.execute(select(Clip).where(Clip.id.in_(list(ids))))).scalars().all()} if ids else {}
    eintraege = []
    for e in p.eintraege or []:
        c = clips.get(e["clip_id"])
        eintraege.append({**e, "clip": _clip_medien_felder(c) if c else None})
    return {"id": str(p.id), "name": p.name, "statistik": p.statistik, "parameter": p.parameter, "eintraege": eintraege}


@router.get("/schnittplan/{plan_id}/bericht", response_class=HTMLResponse)
async def schnittplan_bericht(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Prüfbericht (HTML) zum manuellen Gegenlesen: Skriptzeile → Take/Zeit/gesagter Text/Score/Grund, Lücken."""
    from backend.core.database import SyncSessionLocal
    from backend.core.skript.bericht import html_bericht
    sdb = SyncSessionLocal()
    try:
        p = sdb.query(Schnittplan).filter(Schnittplan.id == plan_id).first()
        if not p:
            raise HTTPException(404, "Schnittplan nicht gefunden.")
        sk = sdb.query(Skript).filter(Skript.id == p.skript_id).first() if p.skript_id else None
        if not sk:
            sk = sdb.query(Skript).filter(Skript.aktiv.is_(True)).first()
        if not sk:
            raise HTTPException(400, "Kein Drehbuch.")
        return HTMLResponse(html_bericht(sdb, p, sk))
    finally:
        sdb.close()
