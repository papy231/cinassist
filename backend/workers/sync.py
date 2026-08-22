"""
CinAssist — Sync-Worker: Ordner-Import (per Referenz), Matching, A/B-Vorschau.

Drei Celery-Tasks + die zugehörigen Service-Funktionen (Sync-Session), damit die
API sie auch eager (`.apply()`) oder in Tests ohne Broker fahren kann:

  cinassist.import_ordner   — Scan + ffprobe/BWF/LTC je Datei → MediaAsset (idempotent via fingerprint)
  cinassist.sync_matchen    — Kaskade (core.sync.matcher) → Take / TakeAudioLink (deterministisch)
  cinassist.sync_vorschau   — leichte Derivate für den A/B-Player (480p-Proxy + Record-Kanal als m4a)

Originale werden nie kopiert oder reencodiert; Derivate liegen unter PROXY_DIR/sync/.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Optional

from backend.core.celery_app import celery_app
from backend.core.config import FFMPEG_BIN, FFPROBE_BIN, PROXY_DIR
from backend.core.database import (
    SyncSessionLocal, Clip, MediaAsset, OrdnerImport, Take, TakeAudioLink,
)
from backend.core.sync import matcher as M
from backend.core.sync.matcher import AssetInfo, matche
from backend.core.sync.namen import NamensTeile
from backend.core.sync.probe import (
    analysiere_audio, analysiere_video, scanne_ordner, verwerfe_identische_container_tc,
    volume_root, volume_uuid,
)
from backend.core.sync.waveform import korreliere_dateien
from backend.workers.ingest import _update_job

logger = logging.getLogger("cinassist.sync")

SYNC_PROXY_DIR = PROXY_DIR / "sync"
SYNC_PROXY_DIR.mkdir(parents=True, exist_ok=True)

ProgressCb = Callable[[int, str], None]


def _noop(_p: int, _m: str) -> None:
    pass


# ═══════════════════════════════════════════════════════════
# 1. Ordner-Import (per Referenz)
# ═══════════════════════════════════════════════════════════

def _asset_aus_probe(db, pr, import_id) -> tuple[MediaAsset, bool]:
    """Upsert nach fingerprint. Rückgabe (asset, neu)."""
    asset = db.query(MediaAsset).filter(MediaAsset.fingerprint == pr.fingerprint).first()
    neu = asset is None
    if neu:
        asset = MediaAsset(id=uuid.uuid4(), fingerprint=pr.fingerprint)
        db.add(asset)
    asset.typ = pr.typ
    asset.pfad = pr.pfad
    asset.dateiname = pr.dateiname
    asset.dauer_s = pr.dauer_s
    asset.sample_rate = pr.sample_rate
    asset.kanaele = pr.kanaele
    asset.fps = pr.fps
    asset.codec = pr.codec
    asset.dateigroesse = pr.dateigroesse
    asset.tc_start = pr.tc_start
    asset.tc_start_s = pr.tc_start_s
    asset.tc_quelle = pr.tc_quelle
    asset.tc_rate = pr.tc_rate
    asset.tc_flag = pr.tc_flag
    asset.ixml_json = pr.ixml_json
    asset.ordner_import_id = import_id
    asset.ltc_kanal = pr.ltc_kanal
    asset.scratch_kanal = pr.scratch_kanal
    asset.record_kanal = pr.record_kanal
    asset.container_tc = pr.container_tc
    asset.szene = pr.namen.szene
    asset.plan = pr.namen.plan
    asset.prise = pr.namen.prise
    asset.unbekannte_markierung = pr.unbekannte_markierung
    asset.datum = pr.datum
    asset.warnungen = list(pr.warnungen)
    asset.aktualisiert_am = datetime.utcnow()
    return asset, neu


def fuehre_import_aus(db, import_id: str, progress: ProgressCb = _noop) -> dict[str, Any]:
    imp = db.query(OrdnerImport).filter(OrdnerImport.id == import_id).first()
    if not imp:
        raise ValueError(f"OrdnerImport {import_id} nicht gefunden")
    imp.status = "laeuft"
    imp.fehler = None
    db.commit()

    scan = scanne_ordner(imp.pfad, imp.typ)
    imp.anzahl_dateien = len(scan.dateien)
    imp.anzahl_ignoriert = scan.ignoriert
    imp.volume_root = volume_root(imp.pfad)
    imp.volume_uuid = volume_uuid(imp.pfad)
    db.commit()
    progress(2, f"{len(scan.dateien)} Dateien gefunden, {scan.ignoriert} ignoriert (._* u. ä.)")

    probes = []
    fehler: list[str] = []
    n = max(1, len(scan.dateien))
    for i, pfad in enumerate(scan.dateien):
        try:
            if imp.typ == "video":
                pr = analysiere_video(pfad, ffprobe_bin=FFPROBE_BIN, ffmpeg_bin=FFMPEG_BIN)
            else:
                pr = analysiere_audio(pfad, ordnername=Path(pfad).parent.name, ffprobe_bin=FFPROBE_BIN)
            probes.append(pr)
        except Exception as e:  # eine kaputte Datei bricht den Import nicht ab
            logger.warning(f"Import: {pfad} übersprungen: {e}")
            fehler.append(f"{Path(pfad).name}: {e}")
        progress(2 + int(90 * (i + 1) / n), f"{i + 1}/{len(scan.dateien)}: {Path(pfad).name}")

    verworfen = verwerfe_identische_container_tc(probes) if imp.typ == "video" else 0

    neu = aktualisiert = 0
    for pr in probes:
        _asset, ist_neu = _asset_aus_probe(db, pr, imp.id)
        neu += int(ist_neu)
        aktualisiert += int(not ist_neu)
    imp.status = "fertig"
    imp.gescannt_am = datetime.utcnow()
    imp.fehler = "\n".join(fehler) if fehler else None
    db.commit()
    ergebnis = {
        "import_id": str(imp.id), "typ": imp.typ, "pfad": imp.pfad,
        "anzahl_dateien": len(scan.dateien), "anzahl_ignoriert": scan.ignoriert,
        "ignoriert_beispiele": scan.ignoriert_beispiele,
        "neu": neu, "aktualisiert": aktualisiert, "fehler": fehler,
        "container_tc_verworfen": verworfen, "volume_uuid": imp.volume_uuid,
    }
    progress(100, f"Import fertig: {neu} neu, {aktualisiert} aktualisiert, {len(fehler)} Fehler")
    return ergebnis


@celery_app.task(bind=True, name="cinassist.import_ordner", max_retries=0)
def import_ordner_task(self, import_id: str, job_id: str) -> dict[str, Any]:
    db = SyncSessionLocal()
    try:
        _update_job(job_id, "laeuft", 1, "Ordner wird gescannt…", schritt="import")
        erg = fuehre_import_aus(db, import_id, lambda p, m: _update_job(job_id, "laeuft", p, m, schritt="import"))
        _update_job(job_id, "fertig", 100, erg and f"Import fertig ({erg['anzahl_dateien']} Dateien)", ergebnis=erg,
                    schritt="import", schritt_daten=erg)
        return erg
    except Exception as e:
        logger.exception("Import fehlgeschlagen")
        try:
            imp = db.query(OrdnerImport).filter(OrdnerImport.id == import_id).first()
            if imp:
                imp.status = "fehler"
                imp.fehler = str(e)
                db.commit()
        finally:
            _update_job(job_id, "fehler", 0, f"Import fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# 2. Matching
# ═══════════════════════════════════════════════════════════

def asset_info(a: MediaAsset) -> AssetInfo:
    return AssetInfo(
        id=str(a.id), typ=a.typ, dateiname=a.dateiname, dauer_s=float(a.dauer_s or 0.0),
        tc_start_s=a.tc_start_s, tc_rate=Fraction(a.tc_rate) if a.tc_rate else None,
        tc_quelle=a.tc_quelle or "keine", datum=a.datum,
        namen=NamensTeile(a.szene, a.plan, a.prise, a.unbekannte_markierung),
        scratch_kanal=a.scratch_kanal, record_kanal=int(a.record_kanal or 0),
        warnungen=list(a.warnungen or []),
    )


def _waveform_fn(pfade: dict[str, str]):
    def fn(a: AssetInfo, v: AssetInfo):
        if v.scratch_kanal is None:
            return None
        try:
            return korreliere_dateien(pfade[a.id], a.record_kanal, pfade[v.id], v.scratch_kanal,
                                      ffmpeg_bin=FFMPEG_BIN)
        except Exception as e:  # nie den Lauf abbrechen
            logger.warning(f"Wellenform {a.dateiname}↔{v.dateiname}: {e}")
            return None
    return fn


def fuehre_matching_aus(db, import_ids: Optional[list[str]] = None, progress: ProgressCb = _noop) -> dict[str, Any]:
    """Kaskade über alle (oder die gewählten Import-)Assets → Takes/Links.

    Manuell bearbeitete Takes (automatisch=False) und Takes, auf die bereits ein Clip zeigt,
    bleiben stehen; ihre Assets werden aus dem Matching herausgenommen. Alle anderen
    automatischen Takes werden gelöscht und neu berechnet (idempotent bei gleicher Eingabe).
    """
    q = db.query(MediaAsset)
    if import_ids:
        q = q.filter(MediaAsset.ordner_import_id.in_([uuid.UUID(str(i)) for i in import_ids]))
    assets = q.order_by(MediaAsset.dateiname, MediaAsset.id).all()
    progress(5, f"{len(assets)} Assets geladen")

    # Geschützte Takes: manuell oder bereits analysiert.
    geschuetzt = db.query(Take).filter(
        (Take.automatisch.is_(False)) | (Take.id.in_(db.query(Clip.take_id).filter(Clip.take_id.isnot(None))))
    ).all()
    gesperrte_assets: set[str] = set()
    for t in geschuetzt:
        if t.video_asset_id:
            gesperrte_assets.add(str(t.video_asset_id))
        for lk in t.audio_links:
            gesperrte_assets.add(str(lk.audio_asset_id))
    geschuetzt_ids = {t.id for t in geschuetzt}

    # Automatische Takes der betroffenen Assets löschen.
    betroffene = {a.id for a in assets}
    alte = db.query(Take).filter(Take.automatisch.is_(True)).all()
    geloescht = 0
    for t in alte:
        if t.id in geschuetzt_ids:
            continue
        beteiligte = ({t.video_asset_id} if t.video_asset_id else set()) | {lk.audio_asset_id for lk in t.audio_links}
        if import_ids and not (beteiligte & betroffene):
            continue
        db.delete(t)
        geloescht += 1
    db.flush()

    videos = [asset_info(a) for a in assets if a.typ == "video" and str(a.id) not in gesperrte_assets]
    audios = [asset_info(a) for a in assets if a.typ == "audio" and str(a.id) not in gesperrte_assets]
    pfade = {str(a.id): a.pfad for a in assets}
    progress(15, f"Matching: {len(videos)} Videos × {len(audios)} Audios")

    erg = matche(videos, audios, waveform_fn=_waveform_fn(pfade))
    progress(80, "Ergebnisse werden gespeichert")

    namen = {str(a.id): a.dateiname for a in assets}
    n_takes = n_links = 0
    for tv in erg.takes:
        t = Take(
            id=uuid.uuid4(),
            video_asset_id=uuid.UUID(tv.video_id) if tv.video_id else None,
            szene=tv.szene, plan=tv.plan, prise=tv.prise, status=tv.status,
            warnungen=list(tv.warnungen), automatisch=True, multicam_gruppe=tv.multicam_gruppe,
            kandidaten_json=[{
                "audio_asset_id": k.audio_id, "video_asset_id": k.video_id,
                "audio_dateiname": namen.get(k.audio_id), "video_dateiname": namen.get(k.video_id),
                "offset_s": k.offset_s, "ueberlappung_s": round(k.ueberlappung_s, 3),
                "ueberlappung_ratio": round(k.ueberlappung_ratio, 3), "begruendung": k.begruendung,
            } for k in tv.kandidaten] or None,
        )
        db.add(t)
        for lk in tv.links:
            db.add(TakeAudioLink(
                id=uuid.uuid4(), take_id=t.id, audio_asset_id=uuid.UUID(lk.audio_id),
                offset_s=lk.offset_s, methode=lk.methode, konfidenz=lk.konfidenz,
                begruendung=lk.begruendung, kanal_fuer_transkription=lk.kanal_fuer_transkription,
                warnungen=list(lk.warnungen), bestaetigt=False,
            ))
            n_links += 1
        for aid in tv.audio_ids_verwaist:
            db.add(TakeAudioLink(
                id=uuid.uuid4(), take_id=t.id, audio_asset_id=uuid.UUID(aid), offset_s=0.0,
                methode="verwaist", konfidenz=0.0,
                begruendung="Kein Video mit überlappendem Timecode gefunden — Audio ohne Bild.",
                kanal_fuer_transkription=next((a.record_kanal for a in audios if a.id == aid), 0),
                warnungen=[], bestaetigt=False,
            ))
        n_takes += 1
    db.commit()
    out = {
        "takes": n_takes, "links": n_links, "geloescht": geloescht, "geschuetzt": len(geschuetzt),
        "statistik": erg.statistik, "warnungen": erg.warnungen,
        "unklar": erg.statistik.get("unklar", 0),
    }
    progress(100, f"Matching fertig: {n_takes} Takes, {n_links} Verknüpfungen, {out['unklar']} unklar")
    return out


@celery_app.task(bind=True, name="cinassist.sync_matchen", max_retries=0)
def sync_matchen_task(self, job_id: str, import_ids: Optional[list[str]] = None) -> dict[str, Any]:
    db = SyncSessionLocal()
    try:
        _update_job(job_id, "laeuft", 1, "Matching startet…", schritt="sync")
        erg = fuehre_matching_aus(db, import_ids, lambda p, m: _update_job(job_id, "laeuft", p, m, schritt="sync"))
        _update_job(job_id, "fertig", 100, "Matching fertig", ergebnis=erg, schritt="sync", schritt_daten=erg)
        return erg
    except Exception as e:
        logger.exception("Matching fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Matching fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# 3. A/B-Vorschau (leichte Derivate)
# ═══════════════════════════════════════════════════════════

def vorschau_video_pfad(asset: MediaAsset) -> Path:
    return SYNC_PROXY_DIR / f"{asset.id}_preview.mp4"


def vorschau_audio_pfad(asset: MediaAsset, kanal: int) -> Path:
    return SYNC_PROXY_DIR / f"{asset.id}_k{kanal}.m4a"


def _kamera_ton_filter(asset: MediaAsset) -> list[str]:
    """Kamera-Ton für die A-Seite: Scratch-Kanal mono; sonst alle Kanäle ohne LTC; sonst Downmix."""
    n = int(asset.kanaele or 0)
    if asset.scratch_kanal is not None:
        return ["-af", f"pan=stereo|c0=c{asset.scratch_kanal}|c1=c{asset.scratch_kanal}"]
    if asset.ltc_kanal is not None and n > 1:
        rest = [k for k in range(n) if k != asset.ltc_kanal]
        expr = "+".join(f"c{k}" for k in rest)
        return ["-af", f"pan=stereo|c0={expr}|c1={expr}"]
    return ["-ac", "2"]


def erzeuge_vorschau(db, take_id: str, progress: ProgressCb = _noop) -> dict[str, Any]:
    take = db.query(Take).filter(Take.id == take_id).first()
    if not take:
        raise ValueError(f"Take {take_id} nicht gefunden")
    out: dict[str, Any] = {"take_id": str(take.id), "video_url": None, "audios": []}
    if take.video_asset_id:
        v = take.video_asset
        ziel = vorschau_video_pfad(v)
        if ziel.exists() and ziel.stat().st_size == 0:
            ziel.unlink()
        if not ziel.exists():
            progress(5, f"Video-Vorschau: {v.dateiname}")
            cmd = [FFMPEG_BIN, "-y", "-nostdin", "-i", v.pfad,
                   "-vf", "scale=-2:480",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                   "-g", "12", "-keyint_min", "12", "-sc_threshold", "0",
                   "-pix_fmt", "yuv420p"]
            if v.kanaele:
                cmd += _kamera_ton_filter(v) + ["-c:a", "aac", "-b:a", "96k"]
            else:
                cmd += ["-an"]
            # Atomar: erst in .part schreiben, dann umbenennen — sonst sieht die API eine
            # halb geschriebene Datei (moov fehlt bis zum Ende) und der Player bleibt schwarz.
            part = ziel.with_name(ziel.stem + ".part.mp4")
            cmd += ["-movflags", "+faststart", "-f", "mp4", str(part)]
            r = subprocess.run(cmd, capture_output=True, timeout=1800)
            if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"Vorschau-Proxy fehlgeschlagen: {r.stderr.decode(errors='replace')[-300:]}")
            part.replace(ziel)
        out["video_url"] = f"/proxies/sync/{ziel.name}"
    links = [lk for lk in take.audio_links]
    for i, lk in enumerate(links):
        a = lk.audio_asset
        k = int(lk.kanal_fuer_transkription or 0)
        ziel = vorschau_audio_pfad(a, k)
        if ziel.exists() and ziel.stat().st_size == 0:
            ziel.unlink()
        if not ziel.exists():
            progress(50 + int(45 * i / max(1, len(links))), f"Audio-Vorschau: {a.dateiname} (Kanal {k})")
            part = ziel.with_name(ziel.stem + ".part.m4a")
            cmd = [FFMPEG_BIN, "-y", "-nostdin", "-i", a.pfad, "-vn",
                   "-af", f"pan=mono|c0=c{k}", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                   "-f", "ipod", str(part)]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"Audio-Vorschau fehlgeschlagen: {r.stderr.decode(errors='replace')[-300:]}")
            part.replace(ziel)
        out["audios"].append({"link_id": str(lk.id), "audio_asset_id": str(a.id),
                              "url": f"/proxies/sync/{ziel.name}", "kanal": k, "offset_s": lk.offset_s})
    progress(100, "Vorschau bereit")
    return out


@celery_app.task(bind=True, name="cinassist.sync_vorschau", max_retries=0)
def sync_vorschau_task(self, take_id: str, job_id: str) -> dict[str, Any]:
    db = SyncSessionLocal()
    try:
        _update_job(job_id, "laeuft", 1, "Vorschau wird erzeugt…", schritt="vorschau")
        erg = erzeuge_vorschau(db, take_id, lambda p, m: _update_job(job_id, "laeuft", p, m, schritt="vorschau"))
        _update_job(job_id, "fertig", 100, "Vorschau bereit", ergebnis=erg, schritt="vorschau", schritt_daten=erg)
        return erg
    except Exception as e:
        logger.exception("Vorschau fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Vorschau fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# 4. Medien-Ordner: Video-Ordner importieren → Clips → Analyse
# ═══════════════════════════════════════════════════════════

def clips_aus_import_erzeugen(db, import_id: str, ordner_id: Optional[str], quelle: str = "A") -> list[Clip]:
    """Pro Video-Asset des Imports einen Clip per Referenz (keine Kopie) im Medien-Ordner anlegen.
    Bereits vorhandene Clips (gleicher dateipfad) werden nicht dupliziert, nur ggf. einsortiert."""
    from backend.core.database import MedienOrdner
    imp = db.query(OrdnerImport).filter(OrdnerImport.id == import_id).first()
    if not imp:
        return []
    ordner = db.query(MedienOrdner).filter(MedienOrdner.id == ordner_id).first() if ordner_id else None
    neu: list[Clip] = []
    assets = db.query(MediaAsset).filter(MediaAsset.ordner_import_id == imp.id, MediaAsset.typ == "video") \
        .order_by(MediaAsset.dateiname).all()
    for a in assets:
        c = db.query(Clip).filter(Clip.dateipfad == a.pfad).first()
        if c is None:
            c = Clip(id=uuid.uuid4(), dateiname=a.dateiname, dateipfad=a.pfad, quelle=quelle,
                     dateigroesse=a.dateigroesse, status="hochgeladen", take_id=None,
                     ordner_id=ordner.id if ordner else None,
                     dauer=a.dauer_s, bildrate=a.fps, codec=a.codec)
            db.add(c)
            neu.append(c)
        elif ordner and c.ordner_id is None:
            c.ordner_id = ordner.id
    db.commit()
    return neu


@celery_app.task(bind=True, name="cinassist.ordner_import_analyse", max_retries=0)
def ordner_import_analyse_task(self, import_id: str, job_id: str, ordner_id: Optional[str],
                               analyse: bool = True, quelle: str = "A") -> dict[str, Any]:
    """Medien-Panel: Ordner scannen → Clips anlegen → (optional) Ingestion je Clip anreihen."""
    from backend.core.database import Job
    from backend.workers.ingest import ingestion_pipeline, proxy_schnell
    db = SyncSessionLocal()
    try:
        _update_job(job_id, "laeuft", 1, "Ordner wird gescannt…", schritt="import")
        erg = fuehre_import_aus(db, import_id, lambda p, m: _update_job(job_id, "laeuft", min(p, 90), m, schritt="import"))
        clips = clips_aus_import_erzeugen(db, import_id, ordner_id, quelle)
        erg["clips_neu"] = len(clips)
        gestartet = []
        if analyse:
            # Zwei Phasen: erst Proxy/Waveform/Strip für alle (schnell abspielbar), dann tiefe Analyse.
            for c in clips:
                pj = Job(id=uuid.uuid4(), typ="proxy", clip_id=c.id, status="wartend", fortschritt=0, nachricht="Vorschau wartet…")
                db.add(pj)
                db.commit()
                pt = proxy_schnell.delay(str(c.id), str(pj.id))
                pj.celery_task_id = pt.id
                db.commit()
            for c in clips:
                job = Job(id=uuid.uuid4(), typ="ingestion", clip_id=c.id, status="wartend", fortschritt=0, nachricht="Analyse wartet…")
                db.add(job)
                db.commit()
                t = ingestion_pipeline.delay(str(c.id), str(job.id))
                job.celery_task_id = t.id
                db.commit()
                gestartet.append({"clip_id": str(c.id), "job_id": str(job.id), "dateiname": c.dateiname})
        erg["analysen"] = gestartet
        _update_job(job_id, "fertig", 100, f"Import fertig: {len(clips)} neue Clips, {len(gestartet)} Analysen gestartet",
                    ergebnis=erg, schritt="import", schritt_daten=erg)
        return erg
    except Exception as e:
        logger.exception("Ordner-Import (Medien) fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Import fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()
