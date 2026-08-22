"""
CinAssist — Import- und Synchronisations-API (Take-Modell)

POST   /api/import/ordner                     → Ordner per Referenz importieren (Celery-Job)
GET    /api/import/ordner                     → Importe auflisten
DELETE /api/import/ordner/{import_id}         → Import + Assets aus der DB entfernen (Dateien bleiben)
POST   /api/sync/run                          → Matching-Kaskade laufen lassen (Celery-Job)
GET    /api/sync/takes                        → Takes (gruppierbar nach Szene/Einstellung) + Links + Kandidaten
GET    /api/sync/assets                       → Assets
POST   /api/sync/takes/{id}/bestaetigen       → manuell bestätigen
POST   /api/sync/takes/{id}/ablehnen          → manuell ablehnen (von der Analyse ausgeschlossen)
POST   /api/sync/takes/{id}/links             → Audio (neu) an Take hängen (methode=manuell)
DELETE /api/sync/links/{link_id}              → Audio abhängen
PATCH  /api/sync/links/{link_id}              → Offset ±ms anpassen
POST   /api/sync/takes/{id}/vorschau          → A/B-Vorschau-Derivate erzeugen (Celery-Job)
POST   /api/sync/analyse-starten              → Clips aus Takes erzeugen + Ingestion starten (blockiert bei `unklar`)
GET    /api/sync/media/clip/{clip_id}         → Original per Referenz (Range)
GET    /api/sync/media/asset/{asset_id}       → Original per Referenz (Range)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import (
    get_db, Clip, Job, MediaAsset, OrdnerImport, Take, TakeAudioLink,
)
from backend.core.sync.probe import volume_gemountet
from backend.workers.ingest import ingestion_pipeline, proxy_schnell
from backend.workers.sync import (
    import_ordner_task, sync_matchen_task, sync_vorschau_task,
    vorschau_audio_pfad, vorschau_video_pfad,
)

router = APIRouter(tags=["Synchronisation"])

STATUS_BLOCKIEREND = {"unklar"}
STATUS_ANALYSIERBAR = {"sicher", "plausibel", "manuell_bestaetigt", "verwaist"}


# ─── Schemas ──────────────────────────────────────────────

class OrdnerImportAnfrage(BaseModel):
    pfad: str = Field(..., description="Absoluter Ordnerpfad (z. B. /Volumes/DSCVR/…/ROHMAT_VIDEO)")
    typ: str = Field(..., pattern="^(video|audio)$")


class SyncRunAnfrage(BaseModel):
    import_ids: Optional[list[str]] = None


class LinkAnlegen(BaseModel):
    audio_asset_id: str
    offset_s: Optional[float] = None
    kanal_fuer_transkription: Optional[int] = None


class LinkAendern(BaseModel):
    offset_s: Optional[float] = None
    kanal_fuer_transkription: Optional[int] = None


# ─── Serialisierung ───────────────────────────────────────

_EXISTS_CACHE: dict[str, tuple[float, bool]] = {}


def _vorhanden(pfad: str, ttl: float = 60.0) -> bool:
    """Existenz ohne Dauerlast auf externen Platten: erst Mountpoint (lokal), dann stat mit 60-s-Cache."""
    import time
    if not volume_gemountet(pfad):
        return False
    jetzt = time.monotonic()
    e = _EXISTS_CACHE.get(pfad)
    if e and jetzt - e[0] < ttl:
        return e[1]
    try:
        ok = Path(pfad).exists()
    except OSError:
        ok = False
    _EXISTS_CACHE[pfad] = (jetzt, ok)
    return ok


def _asset_dict(a: MediaAsset) -> dict:
    return {
        "id": str(a.id), "typ": a.typ, "pfad": a.pfad, "dateiname": a.dateiname,
        "dauer_s": a.dauer_s, "sample_rate": a.sample_rate, "kanaele": a.kanaele, "fps": a.fps,
        "codec": a.codec, "dateigroesse": a.dateigroesse,
        "tc_start": a.tc_start, "tc_start_s": a.tc_start_s, "tc_quelle": a.tc_quelle,
        "tc_rate": a.tc_rate, "tc_flag": a.tc_flag, "container_tc": a.container_tc,
        "ltc_kanal": a.ltc_kanal, "scratch_kanal": a.scratch_kanal, "record_kanal": a.record_kanal,
        "szene": a.szene, "plan": a.plan, "prise": a.prise,
        "unbekannte_markierung": a.unbekannte_markierung,
        "datum": a.datum.isoformat() if a.datum else None,
        "warnungen": a.warnungen or [],
        "ixml": {k: v for k, v in (a.ixml_json or {}).items() if k != "raw"} if a.ixml_json else None,
        "ordner_import_id": str(a.ordner_import_id) if a.ordner_import_id else None,
        "vorhanden": _vorhanden(a.pfad),
        "volume_gemountet": volume_gemountet(a.pfad),
    }


def _derivat_url(p: Optional[Path]) -> Optional[str]:
    """URL eines Vorschau-Derivats mit Cache-Buster (mtime): nach Neu-Erzeugung lädt der Player neu."""
    if not p:
        return None
    try:
        st = p.stat()
    except OSError:
        return None
    if st.st_size == 0:
        return None
    return f"/proxies/sync/{p.name}?v={int(st.st_mtime)}"


def _link_dict(lk: TakeAudioLink) -> dict:
    a = lk.audio_asset
    kanal = int(lk.kanal_fuer_transkription or 0)
    vp = vorschau_audio_pfad(a, kanal) if a else None
    return {
        "id": str(lk.id), "take_id": str(lk.take_id), "audio_asset_id": str(lk.audio_asset_id),
        "audio": _asset_dict(a) if a else None,
        "offset_s": lk.offset_s, "methode": lk.methode, "konfidenz": lk.konfidenz,
        "begruendung": lk.begruendung, "kanal_fuer_transkription": kanal,
        "warnungen": lk.warnungen or [], "bestaetigt": bool(lk.bestaetigt),
        "vorschau_audio_url": _derivat_url(vp),
    }


def _take_dict(t: Take) -> dict:
    v = t.video_asset
    vp = vorschau_video_pfad(v) if v else None
    clip = next(iter(t.clips), None) if t.clips else None
    return {
        "id": str(t.id), "video_asset_id": str(t.video_asset_id) if t.video_asset_id else None,
        "video": _asset_dict(v) if v else None,
        "szene": t.szene, "plan": t.plan, "prise": t.prise, "status": t.status,
        "automatisch": bool(t.automatisch),
        "multicam_gruppe": t.multicam_gruppe,
        "warnungen": t.warnungen or [], "kandidaten": t.kandidaten_json or [],
        "links": [_link_dict(lk) for lk in t.audio_links],
        "clip_id": str(clip.id) if clip else None,
        "clip_status": clip.status if clip else None,
        "vorschau_video_url": _derivat_url(vp),
        "erstellt_am": t.erstellt_am.isoformat() if t.erstellt_am else None,
    }


def _take_query():
    return select(Take).options(
        selectinload(Take.video_asset),
        selectinload(Take.audio_links).selectinload(TakeAudioLink.audio_asset),
        selectinload(Take.clips),
    )


async def _lade_take(db: AsyncSession, take_id: str) -> Take:
    try:
        tid = uuid.UUID(take_id)
    except ValueError:
        raise HTTPException(400, "Ungültige Take-ID")
    t = (await db.execute(_take_query().where(Take.id == tid))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Take nicht gefunden")
    return t


async def _neuer_job(db: AsyncSession, typ: str, nachricht: str, clip_id=None) -> Job:
    job = Job(id=uuid.uuid4(), typ=typ, clip_id=clip_id, status="wartend", fortschritt=0, nachricht=nachricht)
    db.add(job)
    await db.commit()
    return job


# ═══════════════════════════════════════════════════════════
# Import
# ═══════════════════════════════════════════════════════════

_BROWSE_WURZELN = ["/Volumes", str(Path.home()), str(Path.home() / "Movies"), str(Path.home() / "Desktop"),
                   str(Path.home() / "Downloads")]


def _zaehle_medien(ordner: Path, max_tiefe: int = 3, max_dateien: int = 5000) -> tuple[int, int]:
    """Video-/Audio-Dateien bis `max_tiefe` Ebenen tief zählen (Unterordner wie 11-17-23/ mitzählen)."""
    import os
    from backend.core.sync.probe import AUDIO_ENDUNGEN, VIDEO_ENDUNGEN, IGNORIERTE_ORDNER, ist_ignorierte_datei
    v = a = n = 0
    basis = len(ordner.parts)
    for dirpath, dirnames, filenames in os.walk(ordner):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in IGNORIERTE_ORDNER
                       and len(Path(dirpath).parts) - basis < max_tiefe - 1]
        for fn in filenames:
            if ist_ignorierte_datei(fn):
                continue
            ext = Path(fn).suffix.lower()
            v += ext in VIDEO_ENDUNGEN
            a += ext in AUDIO_ENDUNGEN
            n += 1
            if n >= max_dateien:
                return v, a
    return v, a


class OrdnerFinden(BaseModel):
    name: Optional[str] = None            # Ordnername (aus dem Drop), None = nur über Dateinamen suchen
    dateien: list[str] = []               # Dateinamen (rekursiv gesammelt, ohne Pfad)
    typ: str = Field("video", pattern="^(video|audio)$")


@router.post("/api/import/finden")
async def ordner_finden(anfrage: OrdnerFinden):
    """Drag & Drop eines Ordners aus dem Finder liefert dem Browser KEINEN Pfad — nur Name + Dateinamen.
    Wir suchen deshalb unterhalb der Einstiegspunkte (/Volumes, Home …) nach Ordnern gleichen Namens,
    deren Inhalt zu den gedroppten Dateinamen passt, und geben Kandidaten mit Trefferquote zurück."""
    import os
    from backend.core.sync.probe import IGNORIERTE_ORDNER, VIDEO_ENDUNGEN, AUDIO_ENDUNGEN
    gesucht = {d.strip() for d in anfrage.dateien if d and not d.startswith("._")}
    name = (anfrage.name or "").strip() or None
    if not name and len(gesucht) == 0:
        raise HTTPException(400, "Weder Ordnername noch Dateinamen übergeben")
    kandidaten: list[dict] = []
    besucht = 0
    MAX_DIRS = 40000
    endungen = VIDEO_ENDUNGEN if anfrage.typ == "video" else AUDIO_ENDUNGEN

    def _score(pfad: str) -> tuple[float, int]:
        """Anteil der gesuchten Dateinamen, die (bis 2 Ebenen tief) im Ordner liegen; + Anzahl passender Medien."""
        vorhanden: set[str] = set()
        medien = 0
        basis = len(Path(pfad).parts)
        for dp, dns, fns in os.walk(pfad):
            dns[:] = [d for d in dns if not d.startswith(".") and d not in IGNORIERTE_ORDNER and len(Path(dp).parts) - basis < 2]
            for fn in fns:
                if fn.startswith("._"):
                    continue
                vorhanden.add(fn)
                if Path(fn).suffix.lower() in endungen:
                    medien += 1
        if not gesucht:
            return (1.0 if medien else 0.0), medien
        return len(gesucht & vorhanden) / max(1, len(gesucht)), medien

    for wurzel in _BROWSE_WURZELN:
        if not Path(wurzel).is_dir():
            continue
        for dp, dns, fns in os.walk(wurzel):
            besucht += 1
            if besucht > MAX_DIRS:
                break
            dns[:] = [d for d in dns if not d.startswith(".") and d not in IGNORIERTE_ORDNER
                      and d not in ("node_modules", "Library", ".venv", "venv", "__pycache__")]
            # Tiefe begrenzen (Home kann riesig sein)
            if len(Path(dp).parts) - len(Path(wurzel).parts) > 7:
                dns[:] = []
                continue
            treffer_name = name is not None and Path(dp).name == name
            treffer_inhalt = name is None and gesucht and len(gesucht & {f for f in fns if not f.startswith("._")}) >= max(1, int(0.9 * len(gesucht)))
            if treffer_name or treffer_inhalt:
                q, medien = _score(dp)
                if q >= 0.5 or (name is not None and not gesucht):
                    kandidaten.append({"pfad": dp, "quote": round(q, 3), "medien": medien})
                    if len(kandidaten) >= 20:
                        break
        if len(kandidaten) >= 20 or any(k["quote"] >= 0.9 for k in kandidaten):
            break   # sicherer Treffer in dieser Wurzel (z. B. /Volumes) → Home nicht mehr durchsuchen
    # Duplikate (gleicher realer Pfad über verschiedene Wurzeln) entfernen
    gesehen: set[str] = set()
    eindeutig = []
    for k in sorted(kandidaten, key=lambda k: (-k["quote"], -k["medien"], k["pfad"])):
        rp = str(Path(k["pfad"]).resolve())
        if rp in gesehen:
            continue
        gesehen.add(rp)
        eindeutig.append(k)
    return {"name": name, "gesucht": len(gesucht), "kandidaten": eindeutig, "durchsucht": besucht}


@router.get("/api/import/durchsuchen")
async def ordner_durchsuchen(pfad: Optional[str] = None):
    """Ordner-Browser für die UI (lokale App, kein Datei-Upload): Unterordner + Anzahl Video-/Audio-Dateien.

    Ohne `pfad`: Einstiegspunkte (/Volumes, Home, …). Versteckte Ordner und `._*` werden ausgeblendet.
    """
    from backend.core.sync.probe import AUDIO_ENDUNGEN, VIDEO_ENDUNGEN, IGNORIERTE_ORDNER, ist_ignorierte_datei
    if not pfad:
        eintraege = []
        for w in _BROWSE_WURZELN:
            p = Path(w)
            if p.is_dir() and not any(e["pfad"] == str(p) for e in eintraege):
                eintraege.append({"name": p.name or str(p), "pfad": str(p), "videos": 0, "audios": 0, "unterordner": True})
        return {"pfad": None, "eltern": None, "eintraege": eintraege}
    p = Path(pfad).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise HTTPException(404, f"Ordner nicht gefunden: {p}")
    eintraege = []
    videos = audios = 0
    try:
        kinder = sorted(p.iterdir(), key=lambda c: c.name.lower())
    except PermissionError:
        raise HTTPException(403, "Keine Leserechte")
    for c in kinder:
        try:
            if c.is_dir():
                if c.name.startswith(".") or c.name in IGNORIERTE_ORDNER:
                    continue
                v, a = _zaehle_medien(c)
                eintraege.append({"name": c.name, "pfad": str(c), "videos": v, "audios": a, "unterordner": True})
            elif c.is_file() and not ist_ignorierte_datei(c.name):
                ext = c.suffix.lower()
                videos += ext in VIDEO_ENDUNGEN
                audios += ext in AUDIO_ENDUNGEN
        except (PermissionError, OSError):
            continue
    return {"pfad": str(p), "eltern": str(p.parent) if p.parent != p else None,
            "videos": videos, "audios": audios, "eintraege": eintraege}

@router.post("/api/import/ordner")
async def ordner_importieren(anfrage: OrdnerImportAnfrage, db: AsyncSession = Depends(get_db)):
    pfad = Path(anfrage.pfad).expanduser()
    if not pfad.is_absolute():
        raise HTTPException(400, "Bitte einen absoluten Pfad angeben.")
    if not pfad.is_dir():
        raise HTTPException(404, f"Ordner nicht gefunden: {pfad}")
    # Gleicher Ordner + Typ → denselben Import erneut scannen (idempotent, keine Duplikate).
    imp = (await db.execute(select(OrdnerImport).where(OrdnerImport.pfad == str(pfad), OrdnerImport.typ == anfrage.typ)
                            .order_by(OrdnerImport.gescannt_am.desc()))).scalars().first()
    if imp is None:
        imp = OrdnerImport(id=uuid.uuid4(), pfad=str(pfad), typ=anfrage.typ, status="wartend")
        db.add(imp)
    else:
        imp.status = "wartend"
    await db.commit()
    job = await _neuer_job(db, "import", f"Import {anfrage.typ}: {pfad.name}")
    imp.job_id = job.id
    await db.commit()
    task = import_ordner_task.delay(str(imp.id), str(job.id))
    job.celery_task_id = task.id
    await db.commit()
    return {"import_id": str(imp.id), "job_id": str(job.id), "pfad": str(pfad), "typ": anfrage.typ}


@router.get("/api/import/ordner")
async def importe_auflisten(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(OrdnerImport).order_by(OrdnerImport.gescannt_am.desc()))).scalars().all()
    return [{
        "id": str(i.id), "pfad": i.pfad, "typ": i.typ, "status": i.status,
        "gescannt_am": i.gescannt_am.isoformat() if i.gescannt_am else None,
        "anzahl_dateien": i.anzahl_dateien, "anzahl_ignoriert": i.anzahl_ignoriert,
        "volume_uuid": i.volume_uuid, "volume_root": i.volume_root,
        "volume_gemountet": volume_gemountet(i.pfad), "fehler": i.fehler,
        "job_id": str(i.job_id) if i.job_id else None,
    } for i in rows]


@router.delete("/api/import/ordner/{import_id}")
async def import_loeschen(import_id: str, db: AsyncSession = Depends(get_db)):
    """Entfernt Import + zugehörige Assets/Takes aus der DB. Die Originaldateien bleiben unberührt."""
    imp = (await db.execute(select(OrdnerImport).where(OrdnerImport.id == import_id))).scalar_one_or_none()
    if not imp:
        raise HTTPException(404, "Import nicht gefunden")
    assets = (await db.execute(select(MediaAsset).where(MediaAsset.ordner_import_id == imp.id))).scalars().all()
    for a in assets:
        await db.delete(a)   # Takes/Links kaskadieren; Clips (take_id SET NULL) bleiben
    await db.delete(imp)
    await db.commit()
    return {"geloescht": True, "assets": len(assets)}


# ═══════════════════════════════════════════════════════════
# Matching
# ═══════════════════════════════════════════════════════════

@router.post("/api/sync/run")
async def sync_starten(anfrage: SyncRunAnfrage, db: AsyncSession = Depends(get_db)):
    job = await _neuer_job(db, "sync", "Matching wartet…")
    task = sync_matchen_task.delay(str(job.id), anfrage.import_ids)
    job.celery_task_id = task.id
    await db.commit()
    return {"job_id": str(job.id)}


@router.get("/api/sync/takes")
async def takes_auflisten(db: AsyncSession = Depends(get_db)):
    takes = (await db.execute(_take_query().order_by(Take.szene, Take.plan, Take.prise, Take.erstellt_am))).scalars().all()
    daten = [_take_dict(t) for t in takes]
    unklar = [d for d in daten if d["status"] in STATUS_BLOCKIEREND]
    return {
        "takes": daten,
        "anzahl": len(daten),
        "unklar": len(unklar),
        "analyse_blockiert": len(unklar) > 0,
        "status_zaehler": {s: sum(1 for d in daten if d["status"] == s)
                           for s in ("sicher", "plausibel", "unklar", "verwaist", "manuell_bestaetigt", "manuell_abgelehnt")},
    }


@router.get("/api/sync/assets")
async def assets_auflisten(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MediaAsset).order_by(MediaAsset.typ, MediaAsset.dateiname))).scalars().all()
    return [_asset_dict(a) for a in rows]


# ═══════════════════════════════════════════════════════════
# Manuelle Entscheidungen
# ═══════════════════════════════════════════════════════════

@router.post("/api/sync/takes/{take_id}/bestaetigen")
async def take_bestaetigen(take_id: str, db: AsyncSession = Depends(get_db)):
    t = await _lade_take(db, take_id)
    if t.status == "unklar" and not t.audio_links:
        raise HTTPException(409, "Unklarer Take ohne Verknüpfung — bitte zuerst ein Audio anhängen (oder als verwaist bestätigen mit ?ohne_audio=1)")
    t.status = "manuell_bestaetigt"
    t.automatisch = False
    for lk in t.audio_links:
        lk.bestaetigt = True
    t.aktualisiert_am = datetime.utcnow()
    await db.commit()
    return _take_dict(await _lade_take(db, take_id))


@router.post("/api/sync/takes/{take_id}/verwaist-bestaetigen")
async def take_ohne_audio_bestaetigen(take_id: str, db: AsyncSession = Depends(get_db)):
    """Unklarer/verwaister Video-Take wird bewusst ohne Ton freigegeben (Analyse auf Kamera-Ton)."""
    t = await _lade_take(db, take_id)
    if not t.video_asset_id:
        raise HTTPException(409, "Nur für Video-Takes.")
    for lk in list(t.audio_links):
        await db.delete(lk)
    t.status = "manuell_bestaetigt"
    t.automatisch = False
    t.kandidaten_json = None
    t.warnungen = list(t.warnungen or []) + ["Bewusst ohne verknüpften Ton freigegeben — Transkription auf Kamera-Ton"]
    await db.commit()
    return _take_dict(await _lade_take(db, take_id))


@router.post("/api/sync/takes/{take_id}/ablehnen")
async def take_ablehnen(take_id: str, db: AsyncSession = Depends(get_db)):
    t = await _lade_take(db, take_id)
    t.status = "manuell_abgelehnt"
    t.automatisch = False
    t.aktualisiert_am = datetime.utcnow()
    await db.commit()
    return _take_dict(await _lade_take(db, take_id))


async def _entferne_verwaisten_audio_take(db: AsyncSession, audio_asset_id: uuid.UUID) -> None:
    """Hängt ein Audio künftig an einem Video, verschwindet sein Audio-only-Take."""
    rows = (await db.execute(
        _take_query().where(Take.video_asset_id.is_(None))
    )).scalars().all()
    for t in rows:
        if any(lk.audio_asset_id == audio_asset_id for lk in t.audio_links) and len(t.audio_links) == 1:
            await db.delete(t)


async def _stelle_verwaisten_audio_take_her(db: AsyncSession, audio: MediaAsset) -> None:
    andere = (await db.execute(select(TakeAudioLink).where(TakeAudioLink.audio_asset_id == audio.id))).scalars().all()
    if andere:
        return
    t = Take(id=uuid.uuid4(), video_asset_id=None, szene=audio.szene, plan=audio.plan, prise=audio.prise,
             status="verwaist", automatisch=False, warnungen=["Manuell abgehängt — Audio ohne Bild"])
    db.add(t)
    db.add(TakeAudioLink(id=uuid.uuid4(), take_id=t.id, audio_asset_id=audio.id, offset_s=0.0, methode="verwaist",
                         konfidenz=0.0, begruendung="Manuell abgehängt — kein Video zugeordnet.",
                         kanal_fuer_transkription=int(audio.record_kanal or 0), warnungen=[]))


@router.post("/api/sync/takes/{take_id}/links")
async def link_anlegen(take_id: str, anfrage: LinkAnlegen, db: AsyncSession = Depends(get_db)):
    t = await _lade_take(db, take_id)
    if not t.video_asset_id:
        raise HTTPException(409, "Audio-only-Take: bitte das Audio an einen Video-Take hängen (dort „rattacher“).")
    try:
        aid = uuid.UUID(anfrage.audio_asset_id)
    except ValueError:
        raise HTTPException(400, "Ungültige Audio-ID")
    audio = (await db.execute(select(MediaAsset).where(MediaAsset.id == aid, MediaAsset.typ == "audio"))).scalar_one_or_none()
    if not audio:
        raise HTTPException(404, "Audio-Asset nicht gefunden")
    if any(lk.audio_asset_id == aid for lk in t.audio_links):
        raise HTTPException(409, "Audio hängt bereits an diesem Take")

    v = t.video_asset
    offset = anfrage.offset_s
    grund = "Manuell zugeordnet."
    if offset is None:
        if audio.tc_start_s is not None and v.tc_start_s is not None:
            offset = round(audio.tc_start_s - v.tc_start_s, 3)
            grund += f" Offset aus Timecode übernommen ({offset:+.3f} s)."
        else:
            offset = 0.0
            grund += " Kein gemeinsamer Timecode — Offset 0 s angenommen, bitte im A/B-Player prüfen."
    else:
        grund += f" Offset manuell {offset:+.3f} s."
    kanal = anfrage.kanal_fuer_transkription if anfrage.kanal_fuer_transkription is not None else int(audio.record_kanal or 0)
    db.add(TakeAudioLink(id=uuid.uuid4(), take_id=t.id, audio_asset_id=aid, offset_s=float(offset),
                         methode="manuell", konfidenz=1.0, begruendung=grund, kanal_fuer_transkription=kanal,
                         warnungen=[], bestaetigt=True))
    t.status = "manuell_bestaetigt"
    t.automatisch = False
    t.kandidaten_json = None
    await _entferne_verwaisten_audio_take(db, aid)
    await db.commit()
    return _take_dict(await _lade_take(db, take_id))


@router.delete("/api/sync/links/{link_id}")
async def link_loeschen(link_id: str, db: AsyncSession = Depends(get_db)):
    lk = (await db.execute(select(TakeAudioLink).options(selectinload(TakeAudioLink.audio_asset))
                           .where(TakeAudioLink.id == link_id))).scalar_one_or_none()
    if not lk:
        raise HTTPException(404, "Verknüpfung nicht gefunden")
    take_id = lk.take_id
    audio = lk.audio_asset
    await db.delete(lk)
    await db.flush()
    t = await _lade_take(db, str(take_id))
    t.automatisch = False
    if t.video_asset_id:
        if not t.audio_links:
            t.status = "verwaist"
        elif t.status in ("sicher", "plausibel"):
            t.status = "manuell_bestaetigt"
        await _stelle_verwaisten_audio_take_her(db, audio)
        await db.commit()
        return _take_dict(await _lade_take(db, str(take_id)))
    # Audio-only-Take ohne Link → weg
    await db.delete(t)
    await db.commit()
    return {"geloescht": True}


@router.patch("/api/sync/links/{link_id}")
async def link_aendern(link_id: str, anfrage: LinkAendern, db: AsyncSession = Depends(get_db)):
    lk = (await db.execute(select(TakeAudioLink).where(TakeAudioLink.id == link_id))).scalar_one_or_none()
    if not lk:
        raise HTTPException(404, "Verknüpfung nicht gefunden")
    if anfrage.offset_s is not None:
        alt = lk.offset_s
        lk.offset_s = round(float(anfrage.offset_s), 3)
        lk.begruendung = (lk.begruendung or "") + f" Offset manuell angepasst: {alt:+.3f} s → {lk.offset_s:+.3f} s."
    if anfrage.kanal_fuer_transkription is not None:
        lk.kanal_fuer_transkription = int(anfrage.kanal_fuer_transkription)
    lk.bestaetigt = True
    t = await _lade_take(db, str(lk.take_id))
    t.automatisch = False
    if t.status in ("sicher", "plausibel", "unklar"):
        t.status = "manuell_bestaetigt"
    await db.commit()
    return _take_dict(await _lade_take(db, str(lk.take_id)))


# ═══════════════════════════════════════════════════════════
# Vorschau + Analyse
# ═══════════════════════════════════════════════════════════

@router.post("/api/sync/takes/{take_id}/vorschau")
async def vorschau_anfordern(take_id: str, db: AsyncSession = Depends(get_db)):
    t = await _lade_take(db, take_id)
    d = _take_dict(t)
    fertig = (not t.video_asset_id or d["vorschau_video_url"]) and all(l["vorschau_audio_url"] for l in d["links"])
    if fertig:
        return {"fertig": True, "job_id": None, "take": d}
    job = await _neuer_job(db, "vorschau", f"Vorschau Take {take_id[:8]}")
    task = sync_vorschau_task.delay(str(t.id), str(job.id))
    job.celery_task_id = task.id
    await db.commit()
    return {"fertig": False, "job_id": str(job.id), "take": d}


class InMedienAnfrage(BaseModel):
    take_ids: Optional[list[str]] = None
    unklar_bestaetigen: bool = False
    quelle: str = Field("A", pattern="^[AB]$")
    ordnung: str = Field("szene", pattern="^(szene|chronologisch|flach)$")   # Ordnerstruktur im Medien-Panel
    ton_separat: bool = False          # zusätzlich jedes verknüpfte WAV als eigenes Audio-Medium (Ordner „Ton“)
    waisen_video: bool = True          # Bild ohne Ton übernehmen (Kamera-Ton, falls brauchbar)
    waisen_audio: bool = False         # WAV ohne Bild als Audio-Medium übernehmen (Ordner „Nur Ton“)
    analyse: bool = True               # Ingestion sofort starten


# Alias für ältere Aufrufer.
AnalyseStartAnfrage = InMedienAnfrage


async def _ordner(db: AsyncSession, name: str, eltern_id, quelle_pfad: Optional[str] = None):
    from backend.core.database import MedienOrdner
    q = select(MedienOrdner).where(MedienOrdner.name == name)
    q = q.where(MedienOrdner.eltern_id.is_(None)) if eltern_id is None else q.where(MedienOrdner.eltern_id == eltern_id)
    o = (await db.execute(q)).scalars().first()
    if o is None:
        o = MedienOrdner(id=uuid.uuid4(), name=name, eltern_id=eltern_id, quelle_pfad=quelle_pfad)
        db.add(o)
        await db.flush()
    return o


async def _wurzel_fuer_asset(db: AsyncSession, v: MediaAsset):
    imp = (await db.execute(select(OrdnerImport).where(OrdnerImport.id == v.ordner_import_id))).scalar_one_or_none() if v.ordner_import_id else None
    return await _ordner(db, Path(imp.pfad).name if imp else "Synchronisiert", None, imp.pfad if imp else None)


async def _ordner_nach_ordnung(db: AsyncSession, t: Take, v: MediaAsset, ordnung: str):
    """Zielordner je Ordnung — direkt in der Medien-Wurzel, ohne Import-Ordner-Hülle (Wunsch: „Szene 4“ statt
    „ROHMAT_VIDEO/Szene 4“): szene → Szene N/Einstellung M · chronologisch → Drehtag <Datum> · flach → Wurzel."""
    if ordnung == "flach":
        return None
    if ordnung == "chronologisch":
        datum = v.datum
        if datum is None:
            for lk in t.audio_links:
                if lk.audio_asset and lk.audio_asset.datum:
                    datum = lk.audio_asset.datum
                    break
        tag = await _ordner(db, f"Drehtag {datum.isoformat()}" if datum else "Drehtag unbekannt", None)
        return tag.id
    # szene
    if t.szene is None:
        sonst = await _ordner(db, "Ohne Szene", None)
        return sonst.id
    sz = await _ordner(db, f"Szene {t.szene}", None)
    if t.plan is None:
        return sz.id
    ei = await _ordner(db, f"Einstellung {t.plan}", sz.id)
    return ei.id


async def _audio_clip_fuer_asset(db: AsyncSession, a: MediaAsset, ordner_id, quelle: str) -> Clip:
    """Audio-Medium (per Referenz) für ein WAV-Asset — idempotent über dateipfad."""
    c = (await db.execute(select(Clip).where(Clip.dateipfad == a.pfad))).scalars().first()
    if c is None:
        c = Clip(id=uuid.uuid4(), dateiname=a.dateiname, dateipfad=a.pfad, quelle=quelle,
                 dateigroesse=a.dateigroesse, status="hochgeladen", dauer=a.dauer_s, codec=a.codec,
                 ordner_id=ordner_id, hat_bild=False, hat_ton=True)
        db.add(c)
        await db.flush()
    elif c.ordner_id is None:
        c.ordner_id = ordner_id
    return c


async def _erreichbar(pfad: str, timeout: float = 2.0) -> Optional[bool]:
    """os.path.exists mit Timeout: ein hängendes Volume (ExFAT-USB, I/O-Fehler) darf die API nicht blockieren.
    True/False = Antwort, None = Volume antwortet nicht."""
    import asyncio, os
    try:
        return await asyncio.wait_for(asyncio.to_thread(os.path.exists, pfad), timeout)
    except asyncio.TimeoutError:
        return None


async def _ingestion_anstossen(db: AsyncSession, clip: Clip) -> str:
    job = await _neuer_job(db, "ingestion", "Analyse wartet…", clip_id=clip.id)
    task = ingestion_pipeline.delay(str(clip.id), str(job.id))
    job.celery_task_id = task.id
    await db.commit()
    return str(job.id)


async def _proxy_anstossen(db: AsyncSession, clip: Clip) -> str:
    """Phase 1 (Proxy/Waveform/Strip) — wird VOR allen Analysen eingereiht (Worker = FIFO, solo)."""
    job = await _neuer_job(db, "proxy", "Vorschau wartet…", clip_id=clip.id)
    task = proxy_schnell.delay(str(clip.id), str(job.id))
    job.celery_task_id = task.id
    await db.commit()
    return str(job.id)


@router.post("/api/sync/in-medien")
@router.post("/api/sync/analyse-starten")
async def in_medien_uebernehmen(anfrage: InMedienAnfrage, db: AsyncSession = Depends(get_db)):
    """Synchronisierte Takes ins Medien-Panel übernehmen: pro Video-Take EIN Medium (Video mit
    zugeordnetem, synchronem Ton — der Proxy trägt den verknüpften Ton), organisiert nach `ordnung`,
    optional WAVs zusätzlich/als Waisen als Audio-Medien; Ingestion optional sofort.

    Blockiert (409), solange ein betroffener Take `unklar` ist — außer `unklar_bestaetigen=true`.
    """
    q = _take_query()
    if anfrage.take_ids:
        try:
            ids = [uuid.UUID(i) for i in anfrage.take_ids]
        except ValueError:
            raise HTTPException(400, "Ungültige Take-ID")
        q = q.where(Take.id.in_(ids))
    alle = (await db.execute(q)).scalars().all()
    # Reihenfolge Szene › Einstellung › Take (bzw. Dateiname) — so wandern sie auch in die Analyse-Warteschlange.
    alle.sort(key=lambda t: (t.szene if t.szene is not None else 10**6, t.plan if t.plan is not None else 10**6,
                             t.prise if t.prise is not None else 10**6, t.video_asset.dateiname if t.video_asset else ""))
    takes = [t for t in alle if t.video_asset_id and t.status != "manuell_abgelehnt"]
    unklare = [t for t in takes if t.status in STATUS_BLOCKIEREND]
    if unklare and not anfrage.unklar_bestaetigen:
        raise HTTPException(409, {
            "code": "unklar",
            "nachricht": f"{len(unklare)} Take(s) sind unklar — bitte zuerst entscheiden.",
            "take_ids": [str(t.id) for t in unklare],
        })
    if not anfrage.waisen_video:
        takes = [t for t in takes if any(lk.methode != "verwaist" for lk in t.audio_links)]

    gestartet, uebersprungen, medien = [], [], []
    zu_analysieren: list[tuple[Take, Clip]] = []
    volume_tot = False
    for t in takes:
        v = t.video_asset
        if volume_tot:
            uebersprungen.append({"take_id": str(t.id), "grund": "Volume antwortet nicht (Datenträger prüfen / neu einstecken)"})
            continue
        da = await _erreichbar(v.pfad)
        if da is None:
            volume_tot = True
            uebersprungen.append({"take_id": str(t.id), "grund": "Volume antwortet nicht (Datenträger prüfen / neu einstecken)"})
            continue
        if not da:
            uebersprungen.append({"take_id": str(t.id), "grund": "Datei nicht erreichbar (Volume nicht gemountet?)"})
            continue
        vorhanden = next(iter(t.clips), None) if t.clips else None
        ziel_ordner = await _ordner_nach_ordnung(db, t, v, anfrage.ordnung)
        if vorhanden:
            clip = vorhanden
            clip.ordner_id = ziel_ordner
        else:
            clip = (await db.execute(select(Clip).where(Clip.dateipfad == v.pfad))).scalars().first()
            mit_ton = any(lk.methode != "verwaist" for lk in t.audio_links)
            if clip is None:
                clip = Clip(id=uuid.uuid4(), dateiname=v.dateiname, dateipfad=v.pfad, quelle=anfrage.quelle,
                            dateigroesse=v.dateigroesse, status="hochgeladen", take_id=t.id,
                            dauer=v.dauer_s, bildrate=v.fps, codec=v.codec,
                            hat_bild=True, hat_ton=mit_ton or None)   # Etikett sofort: Bild + (verknüpfter) Ton
                db.add(clip)
            else:
                clip.take_id = t.id
                clip.status = "hochgeladen"
                clip.hat_bild = True
                if mit_ton:
                    clip.hat_ton = True
            clip.ordner_id = ziel_ordner
        await db.commit()
        medien.append({"take_id": str(t.id), "clip_id": str(clip.id), "dateiname": v.dateiname,
                       "mit_ton": any(lk.methode != "verwaist" for lk in t.audio_links)})
        # Zusätzlich: WAV(s) als eigene Audio-Medien im Ordner „Ton“ neben dem Video-Ordner
        if anfrage.ton_separat:
            ton_ordner = await _ordner(db, "Ton", ziel_ordner)   # bei flach: „Ton“ in der Wurzel
            for lk in t.audio_links:
                if lk.methode == "verwaist" or not lk.audio_asset or not await _erreichbar(lk.audio_asset.pfad):
                    continue
                ac = await _audio_clip_fuer_asset(db, lk.audio_asset, ton_ordner.id, anfrage.quelle)
                await db.commit()
                if anfrage.analyse and ac.status != "analysiert":
                    gestartet.append({"clip_id": str(ac.id), "job_id": await _ingestion_anstossen(db, ac), "dateiname": ac.dateiname})
        if vorhanden and vorhanden.status == "analysiert":
            uebersprungen.append({"take_id": str(t.id), "clip_id": str(clip.id), "grund": "bereits analysiert (nur einsortiert)"})
            continue
        if anfrage.analyse:
            zu_analysieren.append((t, clip))

    # Zwei-Phasen-Warteschlange: erst Proxy/Waveform/Strip für ALLE (schnell → sofort abspielbar),
    # dann die tiefe Analyse. Der Worker arbeitet FIFO und strikt nacheinander.
    for _t, c in zu_analysieren:
        await _proxy_anstossen(db, c)
    for _t, c in zu_analysieren:
        gestartet.append({"take_id": str(_t.id), "clip_id": str(c.id), "job_id": await _ingestion_anstossen(db, c),
                          "dateiname": c.dateiname, "mit_ton": bool(_t.audio_links)})

    # Waisen-Audio (Ton ohne Bild) als Audio-Medien
    if anfrage.waisen_audio:
        for t in alle:
            if t.video_asset_id or t.status == "manuell_abgelehnt":
                continue
            for lk in t.audio_links:
                a = lk.audio_asset
                if not a or not await _erreichbar(a.pfad):
                    continue
                nur_ton = await _ordner(db, "Nur Ton", None)
                ac = await _audio_clip_fuer_asset(db, a, nur_ton.id, anfrage.quelle)
                await db.commit()
                medien.append({"take_id": str(t.id), "clip_id": str(ac.id), "dateiname": a.dateiname, "mit_ton": True, "nur_ton": True})
                if anfrage.analyse and ac.status != "analysiert":
                    gestartet.append({"clip_id": str(ac.id), "job_id": await _ingestion_anstossen(db, ac), "dateiname": ac.dateiname})
    if volume_tot and not medien:
        raise HTTPException(503, "Der Datenträger mit den Originalen antwortet nicht (Volume ausgeworfen oder I/O-Fehler) — bitte prüfen und erneut versuchen.")
    return {"medien": medien, "gestartet": gestartet, "uebersprungen": uebersprungen, "ordnung": anfrage.ordnung,
            "volume_problem": volume_tot}


# ═══════════════════════════════════════════════════════════
# Zurücksetzen (Neustart des Versuchs)
# ═══════════════════════════════════════════════════════════

class ZuruecksetzenAnfrage(BaseModel):
    clips_loeschen: bool = True       # aus Takes erzeugte Clips (per Referenz) + deren Proxies entfernen
    vorschau_loeschen: bool = True    # A/B-Derivate unter proxies/sync/ entfernen


@router.post("/api/sync/zuruecksetzen")
async def sync_zuruecksetzen(anfrage: ZuruecksetzenAnfrage, db: AsyncSession = Depends(get_db)):
    """Löscht Importe, Assets, Takes, Links (+ optional Sync-Clips und Derivate). Originale bleiben unberührt."""
    from backend.core.config import PROXY_DIR
    from backend.core.medien import clip_stem
    from backend.workers.sync import SYNC_PROXY_DIR
    geloescht = {"clips": 0, "takes": 0, "assets": 0, "importe": 0, "derivate": 0}
    if anfrage.clips_loeschen:
        clips = (await db.execute(select(Clip).where(Clip.take_id.isnot(None)))).scalars().all()
        for c in clips:
            stem = clip_stem(c)
            for suffix in ("_proxy.mp4", "_wf.png", "_strip.jpg"):
                p = PROXY_DIR / f"{stem}{suffix}"
                if p.exists():
                    p.unlink(); geloescht["derivate"] += 1
            await db.delete(c)          # Szenen/Jobs kaskadieren; Originaldatei bleibt (per Referenz)
            geloescht["clips"] += 1
    takes = (await db.execute(select(Take))).scalars().all()
    for t in takes:
        await db.delete(t)
        geloescht["takes"] += 1
    assets = (await db.execute(select(MediaAsset))).scalars().all()
    for a in assets:
        await db.delete(a)
        geloescht["assets"] += 1
    importe = (await db.execute(select(OrdnerImport))).scalars().all()
    for i in importe:
        await db.delete(i)
        geloescht["importe"] += 1
    await db.commit()
    if anfrage.vorschau_loeschen and SYNC_PROXY_DIR.exists():
        for p in SYNC_PROXY_DIR.iterdir():
            if p.is_file():
                p.unlink(); geloescht["derivate"] += 1
    return {"geloescht": geloescht}


# ═══════════════════════════════════════════════════════════
# Medien per Referenz (Range)
# ═══════════════════════════════════════════════════════════

async def _erlaubte_wurzeln(db: AsyncSession) -> list[Path]:
    rows = (await db.execute(select(OrdnerImport.pfad))).scalars().all()
    return [Path(p).resolve() for p in rows]


def _serve_referenz(pfad: str, wurzeln: list[Path], request: Request):
    from backend.main import _serve_range  # zur Laufzeit, kein Import-Zyklus
    p = Path(pfad).resolve()
    for w in wurzeln:
        try:
            rel = p.relative_to(w)
        except ValueError:
            continue
        return _serve_range(w, str(rel), request)
    raise HTTPException(403, "Pfad liegt außerhalb der importierten Ordner")


@router.get("/api/sync/media/asset/{asset_id}")
async def media_asset(asset_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    a = (await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Asset nicht gefunden")
    if not Path(a.pfad).exists():
        raise HTTPException(503, "Datei nicht erreichbar — Volume nicht gemountet?" if not volume_gemountet(a.pfad) else "Datei fehlt")
    return _serve_referenz(a.pfad, await _erlaubte_wurzeln(db), request)


@router.get("/api/sync/media/clip/{clip_id}")
async def media_clip(clip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Clip).where(Clip.id == clip_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Clip nicht gefunden")
    if not Path(c.dateipfad).exists():
        raise HTTPException(503, "Datei nicht erreichbar — Volume nicht gemountet?" if not volume_gemountet(c.dateipfad) else "Datei fehlt")
    return _serve_referenz(c.dateipfad, await _erlaubte_wurzeln(db), request)
