"""
CinAssist — Clips API (Upload + Analyse-Endpunkte)

POST /api/clips/upload        → Video hochladen (A oder B)
GET  /api/clips                → Alle Clips auflisten
GET  /api/clips/{clip_id}      → Clip-Details
GET  /api/clips/{clip_id}/analyse → Analyse-Ergebnisse (Szenen, Transkription)
DELETE /api/clips/{clip_id}    → Clip löschen
"""

import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import UPLOAD_DIR, PROXY_DIR, SCENE_THRESHOLD
from backend.core.database import get_db, Clip, Szene, Job
from backend.core.medien import clip_stem, clip_video_url, ist_upload_datei, medientyp, medienart, proxy_dateiname, AUDIO_ENDUNGEN_UPLOAD
from backend.workers.ingest import ingestion_pipeline

router = APIRouter(prefix="/api/clips", tags=["Clips"])

# Erlaubte Dateiformate
ERLAUBTE_ENDUNGEN = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mxf"} | AUDIO_ENDUNGEN_UPLOAD
MAX_DATEIGROESSE = 5 * 1024 * 1024 * 1024  # 5 GB


def _proxy_url(name: str) -> str:
    """/proxies/<name>?v=<mtime> — Cache-Buster: Proxies/Waveforms werden bei Bedarf neu gebaut (stummer Proxy, zu kurz),
    der Browser hält sie aber bis zu 1 h (Cache-Control). Die Versionsnummer = Änderungszeit der Datei."""
    try:
        v = int((PROXY_DIR / name).stat().st_mtime)
    except OSError:
        v = 0
    return f"/proxies/{name}?v={v}"


def _nonempty(p: Path) -> bool:
    """True nur wenn die Datei existiert UND nicht leer ist.

    Fehlgeschlagene FFmpeg-Läufe hinterlassen 0-Byte-Proxies; die dürfen
    NIE als proxy_url ausgeliefert werden (Range-Request → 416 → schwarzer
    Player im Frontend).
    """
    try:
        return p.exists() and p.stat().st_size > 0
    except OSError:
        return False


# ─── Upload ──────────────────────────────────────────────

@router.post("/upload")
async def clip_hochladen(
    datei: UploadFile = File(...),
    quelle: str = Form(..., description="'A' oder 'B'"),
    ueberschreiben: bool = Form(False, description="Vorhandenen Clip gleichen Namens ersetzen"),
    ordner_id: str | None = Form(None, description="Medien-Ordner (Bin), in den der Clip einsortiert wird"),
    db: AsyncSession = Depends(get_db),
):
    """
    Video hochladen und automatische Analyse starten.

    - Speichert in /uploads/{clip_id}.{ext}
    - Startet Ingestion-Job (Celery)
    - Gibt job_id zurück für WebSocket-Tracking
    """
    # Validierung: Quelle
    if quelle not in ("A", "B"):
        raise HTTPException(400, "Quelle muss 'A' oder 'B' sein.")

    # Validierung: Dateiformat
    dateiname = datei.filename or "unbekannt.mp4"
    endung = Path(dateiname).suffix.lower()
    if endung not in ERLAUBTE_ENDUNGEN:
        raise HTTPException(
            400,
            f"Dateiformat '{endung}' nicht unterstützt. "
            f"Erlaubt: {', '.join(ERLAUBTE_ENDUNGEN)}"
        )

    # Validierung: Dateigröße (Content-Length Header)
    if datei.size and datei.size > MAX_DATEIGROESSE:
        raise HTTPException(400, "Datei zu groß (max. 5 GB).")

    # Duplikat-Prüfung nach Dateiname. Bei vorhandenem Namen ohne `ueberschreiben`
    # → 409 mit strukturiertem detail, damit das Frontend "Ersetzen" anbieten kann.
    vorhandene = list(
        (await db.execute(select(Clip).where(Clip.dateiname == dateiname))).scalars().all()
    )
    if vorhandene and not ueberschreiben:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_name",
                "dateiname": dateiname,
                "existing_clip_id": str(vorhandene[0].id),
                "anzahl": len(vorhandene),
            },
        )
    if vorhandene and ueberschreiben:
        for alt in vorhandene:
            try:
                alt_pfad = Path(alt.dateipfad)
                if alt_pfad.exists() and not alt.take_id and ist_upload_datei(alt.dateipfad):
                    alt_pfad.unlink()
                alt_thumbs = Path(f"temp/thumbs_{alt.id}")
                if alt_thumbs.exists():
                    shutil.rmtree(alt_thumbs)
            except Exception:
                pass
            await db.delete(alt)
        await db.commit()

    # Datei speichern
    clip_id = str(uuid.uuid4())
    ziel_pfad = UPLOAD_DIR / f"{clip_id}{endung}"

    try:
        with open(ziel_pfad, "wb") as f:
            while chunk := await datei.read(1024 * 1024):  # 1MB Chunks
                f.write(chunk)
    except Exception as e:
        # Aufräumen bei Fehler
        ziel_pfad.unlink(missing_ok=True)
        raise HTTPException(500, f"Datei konnte nicht gespeichert werden: {e}")

    dateigroesse = ziel_pfad.stat().st_size

    # Metadaten sofort per ffprobe (< 1 s) — sonst steht der Clip mit „0 s“ im Medien-Panel und lässt
    # sich nicht auf die Timeline ziehen, bis der (ggf. lange belegte) Worker den Metadaten-Schritt macht.
    meta: dict = {}
    try:
        import asyncio
        from backend.workers.ingest import _get_video_info
        meta = await asyncio.to_thread(_get_video_info, str(ziel_pfad))
    except Exception:
        meta = {}

    # Clip in DB anlegen
    clip = Clip(
        id=clip_id,
        dateiname=dateiname,
        dateipfad=str(ziel_pfad),
        quelle=quelle,
        dateigroesse=dateigroesse,
        status="hochgeladen",
        dauer=meta.get("dauer") or None,
        aufloesung=meta.get("aufloesung") or None,
        bildrate=meta.get("bildrate") or None,
        codec=meta.get("codec") or None,
    )
    if ordner_id:
        try:
            clip.ordner_id = uuid.UUID(ordner_id)
        except ValueError:
            pass
    db.add(clip)

    # Job anlegen
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        typ="ingestion",
        clip_id=clip_id,
        status="wartend",
        fortschritt=0,
        nachricht="Job wurde erstellt, warte auf Start...",
    )
    db.add(job)
    await db.commit()

    # Celery-Task starten
    task = ingestion_pipeline.delay(clip_id, job_id)

    # Task-ID speichern
    job.celery_task_id = task.id
    await db.commit()

    return {
        "clip_id": clip_id,
        "job_id": job_id,
        "dateiname": dateiname,
        "quelle": quelle,
        "groesse_mb": round(dateigroesse / (1024 * 1024), 1),
        "nachricht": "Video hochgeladen. Analyse wird gestartet...",
    }


# ─── Alle Clips ─────────────────────────────────────────

@router.get("")
async def clips_auflisten(db: AsyncSession = Depends(get_db)):
    """Alle hochgeladenen Clips auflisten."""
    from backend.core.database import Take, TakeAudioLink
    result = await db.execute(
        select(Clip).order_by(Clip.erstellt_am.desc())
        .options(selectinload(Clip.take).selectinload(Take.audio_links).selectinload(TakeAudioLink.audio_asset),
                 selectinload(Clip.take).selectinload(Take.video_asset))
    )
    clips = result.scalars().all()

    def _sync_info(clip):
        t = clip.take
        if not t:
            return None
        links = [lk for lk in t.audio_links if lk.methode != "verwaist" and lk.audio_asset]
        links.sort(key=lambda lk: (not bool(lk.bestaetigt), -(lk.konfidenz or 0.0)))
        return {
            "take_id": str(t.id), "status": t.status, "multicam_gruppe": t.multicam_gruppe,
            "szene": t.szene, "plan": t.plan, "prise": t.prise,
            "tc_start": t.video_asset.tc_start if t.video_asset else None,
            "tc_start_s": t.video_asset.tc_start_s if t.video_asset else None,
            "ton": {"dateiname": links[0].audio_asset.dateiname, "offset_s": links[0].offset_s,
                    "methode": links[0].methode, "konfidenz": links[0].konfidenz} if links else None,
        }

    return [
        {
            "id": str(clip.id),
            "dateiname": clip.dateiname,
            "quelle": clip.quelle,
            "dauer": clip.dauer,
            "aufloesung": clip.aufloesung,
            "bildrate": clip.bildrate,
            "dateigroesse_mb": round(clip.dateigroesse / (1024 * 1024), 1) if clip.dateigroesse else None,
            "status": clip.status,
            "erstellt_am": clip.erstellt_am.isoformat() if clip.erstellt_am else None,
            "video_url": clip_video_url(clip),
            "medientyp": medientyp(clip.dateipfad),
            "medienart": medienart(clip),        # video | audio | av (Etikett aus der Ingestion)
            "hat_bild": clip.hat_bild,
            "hat_ton": clip.hat_ton,
            "proxy_url": (
                _proxy_url(proxy_dateiname(clip))
                if clip.dateipfad and _nonempty(PROXY_DIR / proxy_dateiname(clip))
                else None
            ),
            "waveform_url": (
                _proxy_url(f"{clip_stem(clip)}_wf.png")
                if clip.dateipfad and (PROXY_DIR / f"{clip_stem(clip)}_wf.png").exists()
                else None
            ),
            "strip_url": (
                _proxy_url(f"{clip_stem(clip)}_strip.jpg")
                if clip.dateipfad and (PROXY_DIR / f"{clip_stem(clip)}_strip.jpg").exists()
                else None
            ),
            "dateipfad": clip.dateipfad,
            "ordner_id": str(clip.ordner_id) if clip.ordner_id else None,
            "take_id": str(clip.take_id) if clip.take_id else None,
            "sync": _sync_info(clip),
        }
        for clip in clips
    ]


def _clip_medien_felder(clip: Clip) -> dict:
    """Die Medien-Felder eines Clips (URLs/Etikett), wie sie die Liste liefert — für andere Router (Schnittplan)."""
    return {
        "id": str(clip.id), "dateiname": clip.dateiname, "dauer": clip.dauer, "status": clip.status,
        "video_url": clip_video_url(clip), "medientyp": medientyp(clip.dateipfad), "medienart": medienart(clip),
        "hat_bild": clip.hat_bild, "hat_ton": clip.hat_ton,
        "proxy_url": (_proxy_url(proxy_dateiname(clip)) if clip.dateipfad and _nonempty(PROXY_DIR / proxy_dateiname(clip)) else None),
        "waveform_url": (_proxy_url(f"{clip_stem(clip)}_wf.png") if clip.dateipfad and (PROXY_DIR / f"{clip_stem(clip)}_wf.png").exists() else None),
        "strip_url": (_proxy_url(f"{clip_stem(clip)}_strip.jpg") if clip.dateipfad and (PROXY_DIR / f"{clip_stem(clip)}_strip.jpg").exists() else None),
    }


# ─── Clip-Details ────────────────────────────────────────

@router.get("/{clip_id}")
async def clip_details(clip_id: str, db: AsyncSession = Depends(get_db)):
    """Detailinformationen zu einem Clip."""
    result = await db.execute(
        select(Clip).where(Clip.id == clip_id)
    )
    clip = result.scalar_one_or_none()

    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")

    return {
        "id": str(clip.id),
        "dateiname": clip.dateiname,
        "dateipfad": clip.dateipfad,
        "quelle": clip.quelle,
        "dauer": clip.dauer,
        "aufloesung": clip.aufloesung,
        "bildrate": clip.bildrate,
        "codec": clip.codec,
        "dateigroesse_mb": round(clip.dateigroesse / (1024 * 1024), 1) if clip.dateigroesse else None,
        "status": clip.status,
        "erstellt_am": clip.erstellt_am.isoformat() if clip.erstellt_am else None,
        "video_url": clip_video_url(clip),
        "medientyp": medientyp(clip.dateipfad),
        "medienart": medienart(clip),
    }


# ─── Analyse-Ergebnisse ─────────────────────────────────

@router.get("/{clip_id}/analyse")
async def clip_analyse(clip_id: str, db: AsyncSession = Depends(get_db)):
    """
    Alle Analyse-Ergebnisse eines Clips:
    - Szenen mit Timestamps
    - Transkription pro Szene
    - CLIP-Beschreibungen
    - Embedding-Status
    """
    result = await db.execute(
        select(Clip)
        .where(Clip.id == clip_id)
        .options(selectinload(Clip.szenen))
    )
    clip = result.scalar_one_or_none()

    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")

    if clip.status != "analysiert":
        raise HTTPException(
            409,
            f"Clip-Analyse nicht abgeschlossen. Status: {clip.status}"
        )

    szenen = sorted(clip.szenen, key=lambda s: s.szenen_nr)

    return {
        "clip_id": str(clip.id),
        "dateiname": clip.dateiname,
        "quelle": clip.quelle,
        "dauer": clip.dauer,
        "aufloesung": clip.aufloesung,
        "bildrate": clip.bildrate,
        "szenen_anzahl": len(szenen),
        "szenen": [
            {
                "id": str(s.id),
                "szenen_nr": s.szenen_nr,
                "start": s.start_zeit,
                "end": s.end_zeit,
                "dauer": s.dauer,
                "beschreibung": s.beschreibung,
                "transkription": s.transkription,
                "transkription_json": s.transkription_json,
                "hat_embedding": s.clip_embedding is not None and any(v != 0.0 for v in s.clip_embedding),
                "thumbnail_pfad": s.thumbnail_pfad,
                "framing": s.framing,
                "face_count": s.face_count,
                "personen": (s.analyse_visuelle or {}).get("personen") if isinstance(s.analyse_visuelle, dict) else None,
                "stichproben": (s.analyse_visuelle or {}).get("stichproben") if isinstance(s.analyse_visuelle, dict) else None,
            }
            for s in szenen
        ],
    }


# ─── Pipeline-Bericht (post-mortem) ─────────────────────

@router.get("/{clip_id}/pipeline")
async def clip_pipeline_bericht(clip_id: str, db: AsyncSession = Depends(get_db)):
    """
    Rekonstruiert den vollständigen Pipeline-Bericht eines analysierten Clips
    direkt aus dem Datenbank-Zustand (keine separate Persistierung nötig).

    Liefert für jeden der 9 Pipeline-Schritte ein `schritt_daten`-Dict mit
    den konkreten Belegen (Auflösung, Schwellwert, Embedding-Anzahl, etc.).

    Format der Antwort:
        {
            "clip_id": "...",
            "dateiname": "...",
            "schritt_history": {
                "metadaten":        { ... },
                "proxy":            { ... },
                "audio":            { ... },
                "transkription":    { ... },
                "szenenerkennung":  { ... },
                "visuelle_analyse": { ... },
                "clip":             { ... },
                "beschreibungen":   { ... },
                "persistierung":    { ... }
            }
        }
    """
    result = await db.execute(
        select(Clip).where(Clip.id == clip_id).options(selectinload(Clip.szenen))
    )
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")

    szenen = sorted(clip.szenen, key=lambda s: s.szenen_nr)
    history: dict[str, dict] = {}

    # 1. Metadaten — aus dem clips-Eintrag
    if clip.dauer:
        history["metadaten"] = {
            "dauer_s": round(clip.dauer, 1),
            "aufloesung": clip.aufloesung,
            "bildrate": clip.bildrate,
            "codec": clip.codec,
            "tool": "ffprobe",
        }

    # 2. Proxy — Dateigröße aus dem Dateisystem ablesen
    if clip.dateipfad:
        proxy_pfad = PROXY_DIR / proxy_dateiname(clip)
        if proxy_pfad.exists():
            history["proxy"] = {
                "size_mb": round(proxy_pfad.stat().st_size / (1024 * 1024), 2),
                "ziel_aufloesung": "max 960px",
                "codec": "H.264 / AAC",
                "preset": "fast, CRF 26",
            }

    # 3. Audio — die WAV-Datei wird nach Transkription gelöscht;
    #    wir wissen aber, dass sie existierte, wenn es Transkriptionen gibt
    hat_transkription = any(s.transkription for s in szenen)
    # 2b. Sync — verknüpfter Ton aus dem Take-Modell (Clip per Referenz)
    ton_quelle = "Kamera-Ton"
    if clip.take_id:
        from backend.core.database import Take, TakeAudioLink
        take = (await db.execute(
            select(Take).where(Take.id == clip.take_id)
            .options(selectinload(Take.audio_links).selectinload(TakeAudioLink.audio_asset),
                     selectinload(Take.video_asset))
        )).scalar_one_or_none()
        if take:
            links = [lk for lk in take.audio_links if lk.methode != "verwaist" and lk.audio_asset]
            links.sort(key=lambda lk: (not bool(lk.bestaetigt), -(lk.konfidenz or 0.0)))
            history["sync"] = {
                "take_id": str(take.id), "take_status": take.status,
                "video": take.video_asset.dateiname if take.video_asset else None,
                "video_tc": take.video_asset.tc_start if take.video_asset else None,
                "video_tc_quelle": take.video_asset.tc_quelle if take.video_asset else None,
                "audio": links[0].audio_asset.dateiname if links else None,
                "audio_tc": links[0].audio_asset.tc_start if links else None,
                "kanal": links[0].kanal_fuer_transkription if links else None,
                "offset_s": links[0].offset_s if links else None,
                "methode": links[0].methode if links else None,
                "konfidenz": links[0].konfidenz if links else None,
                "begruendung": links[0].begruendung if links else None,
                "warnung": None if links else "Kein verknüpfter Ton — Transkription auf Kamera-Ton",
            }
            if links and take.status not in ("unklar", "manuell_abgelehnt"):
                ton_quelle = f"verknüpftes WAV ({links[0].audio_asset.dateiname}, Kanal {links[0].kanal_fuer_transkription}, Offset {links[0].offset_s:+.3f} s)"
    if hat_transkription or szenen:
        history["audio"] = {
            "format": "WAV PCM 16-bit (temporär, nach Transkription gelöscht)",
            "sample_rate": 16000,
            "channels": 1,
            "quelle": ton_quelle,
        }

    # 4. Transkription — alle Szenen-Segmente aggregieren
    if hat_transkription:
        alle_segmente = []
        for s in szenen:
            if s.transkription_json:
                alle_segmente.extend(s.transkription_json)
        woerter_gesamt = sum(len(seg.get("woerter", [])) for seg in alle_segmente)
        text_gesamt = " ".join(s.transkription for s in szenen if s.transkription).strip()
        history["transkription"] = {
            "segmente": len(alle_segmente),
            "woerter": woerter_gesamt,
            "modell": "whisper-large-v3-turbo",
            # text_komplett: vollständiger Text, im Modal entfaltet angezeigt
            "text_komplett": text_gesamt or "(keine Sprache erkannt)",
        }

    # 5. Szenenerkennung — aus den Szenen-Einträgen
    if szenen:
        dauern = [s.dauer for s in szenen]
        history["szenenerkennung"] = {
            "szenen": len(szenen),
            "algorithmus": "PySceneDetect ContentDetector (HSV)",
            "threshold": SCENE_THRESHOLD,
            "min_dauer_s": round(min(dauern), 2),
            "max_dauer_s": round(max(dauern), 2),
            "avg_dauer_s": round(sum(dauern) / len(dauern), 2),
        }

    # 6. Visuelle Analyse — aus dem analyse_visuelle-JSON-Feld
    szenen_mit_va = [s for s in szenen if s.analyse_visuelle]
    if szenen_mit_va:
        energien = [float(s.analyse_visuelle.get("energie", 0) or 0) for s in szenen_mit_va]
        history["visuelle_analyse"] = {
            "szenen_analysiert": len(szenen_mit_va),
            "frames_pro_szene": 3,
            "metriken": ["helligkeit", "kontrast", "bewegung", "schärfe", "energie"],
            "energie_min": round(min(energien), 3) if energien else None,
            "energie_max": round(max(energien), 3) if energien else None,
            "energie_avg": round(sum(energien) / len(energien), 3) if energien else None,
        }

    # 7. CLIP-Embeddings — Anzahl der nicht-Null-Vektoren
    szenen_mit_emb = [
        s for s in szenen
        if s.clip_embedding and any(v != 0.0 for v in s.clip_embedding)
    ]
    if szenen_mit_emb:
        history["clip"] = {
            "embeddings": len(szenen_mit_emb),
            "embeddings_nonzero": len(szenen_mit_emb),
            "dimension": 512,
            "modell": "ViT-B/32 (OpenAI, open_clip)",
        }

    # 8. Beschreibungen — von LLaMA3 generierte Sätze (Fallback "Szene N: Xs" ausgeschlossen)
    szenen_mit_desc = [
        s for s in szenen
        if s.beschreibung and not s.beschreibung.startswith("Szene ")
    ]
    if szenen_mit_desc:
        # alle: vollständige Liste pro Szene (Szenennummer + Beschreibung), im Modal entfaltet
        alle_beschreibungen = [
            f"Szene {s.szenen_nr}: {s.beschreibung}"
            for s in szenen_mit_desc
        ]
        from backend.core.config import OLLAMA_MODEL
        history["beschreibungen"] = {
            "beschreibungen": len(szenen_mit_desc),
            "modell": OLLAMA_MODEL,
            "provider": "Ollama (lokal)",
            "alle": alle_beschreibungen,
        }

    # 9. Persistierung — der reine Fakt, dass die Szenen in der DB sind
    if szenen:
        history["persistierung"] = {
            "szenen_gespeichert": len(szenen),
            "tabellen": ["clips (UPDATE)", "szenen (INSERT)"],
            "datenbank": "PostgreSQL",
        }

    # ─── Pro-Szene-Detail (Rohdaten für millimetergenaue Inspektion) ─────
    import math as _math
    szenen_detail = []
    for s in szenen:
        emb_norm: float | None = None
        emb_dim: int | None = None
        if s.clip_embedding and any(v != 0.0 for v in s.clip_embedding):
            emb_dim = len(s.clip_embedding)
            emb_norm = round(
                _math.sqrt(sum(float(v) * float(v) for v in s.clip_embedding)),
                4,
            )

        # Wort-Timestamps aus dem Whisper-JSON extrahieren (alle Wörter dieser Szene)
        woerter_zeitstempel: list[dict] = []
        if s.transkription_json:
            for seg in s.transkription_json:
                for w in seg.get("woerter", []):
                    woerter_zeitstempel.append({
                        "wort": w.get("wort", ""),
                        "start": w.get("start"),
                        "end": w.get("end"),
                    })

        thumb_url: str | None = None
        if s.thumbnail_pfad:
            # Thumbnail liegt in temp/ — wird statisch unter /temp/ ausgeliefert
            thumb_name = Path(s.thumbnail_pfad).name
            thumb_dir = Path(s.thumbnail_pfad).parent.name
            thumb_url = f"/temp/{thumb_dir}/{thumb_name}"

        szenen_detail.append({
            "id": str(s.id),
            "szenen_nr": s.szenen_nr,
            "start_zeit": s.start_zeit,
            "end_zeit": s.end_zeit,
            "dauer": s.dauer,
            "thumbnail_url": thumb_url,
            "transkription": s.transkription,
            "transkription_segmente": s.transkription_json,  # Roh-Segmente von Whisper
            "woerter_zeitstempel": woerter_zeitstempel,      # flache Liste aller Wörter
            "beschreibung": s.beschreibung,                  # LLaMA3 — vollständige Antwort
            "analyse_visuelle": s.analyse_visuelle,          # PIL-Metriken (Rohwerte)
            "embedding_vorhanden": emb_dim is not None,
            "embedding_dimension": emb_dim,
            "embedding_norm": emb_norm,
        })

    return {
        "clip_id": str(clip.id),
        "dateiname": clip.dateiname,
        "schritt_history": history,
        "szenen_detail": szenen_detail,
    }


# ─── Kontextuelle Synthese (LLM-basiert, cached) ─────────

@router.get("/{clip_id}/synthese")
async def clip_synthese(
    clip_id: str,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Erzeugt eine inhaltliche Zusammenfassung des Clips oder holt sie aus dem Zwischenspeicher:
    thème, narratif, style visuel, ambiance, genre, personnes présentes.

    Mit `?refresh=true` wird eine Neuberechnung erzwungen.
    Das Ergebnis wird in `clips.synthese_json` abgelegt.
    """
    import datetime as _dt
    import httpx
    import json as _json
    from collections import Counter
    from backend.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
    from backend.core.database import Speaker

    result = await db.execute(
        select(Clip).where(Clip.id == clip_id).options(selectinload(Clip.szenen))
    )
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")

    if clip.synthese_json and not refresh:
        return {"clip_id": str(clip.id), "cached": True, "synthese": clip.synthese_json}

    # Tatsächlich getrennte Sprecher dieses Clips; maßgeblich für die anwesenden Personen.
    speakers_result = await db.execute(select(Speaker).where(Speaker.clip_id == clip_id))
    speakers = list(speakers_result.scalars().all())
    speaker_labels = [sp.label_manual or sp.label_auto for sp in speakers]

    szenen = sorted(clip.szenen, key=lambda s: s.szenen_nr)
    # SPEAKER_00 → „Sprecher A“ (bzw. manuell vergebener Name), damit das Modell Turns unterscheiden kann.
    sprecher_namen: dict[str, str] = {}
    for i, sp in enumerate(sorted(speakers, key=lambda x: x.label_auto)):
        sprecher_namen[sp.label_auto] = sp.label_manual or f"Sprecher {chr(65 + i)}"

    # ── Belege sammeln (nur was wirklich in den Daten steht) ──
    transkript_lines: list[str] = []
    seg_seen: set[tuple[float, float]] = set()
    beschreibungen: list[str] = []
    framings: list[str] = []
    personen_max = 0
    for s in szenen:
        av = s.analyse_visuelle if isinstance(s.analyse_visuelle, dict) else {}
        proben = av.get("stichproben") or []
        if proben:
            for pr in proben:
                if pr.get("beschreibung"):
                    beschreibungen.append(f"[{float(pr.get('t', s.start_zeit)):.0f}s] {pr['beschreibung']}")
        elif s.beschreibung:
            beschreibungen.append(f"[{s.start_zeit:.0f}s] {s.beschreibung}")
        if isinstance(av.get("personen"), int):
            personen_max = max(personen_max, av["personen"])
        if s.framing:
            framings.append(s.framing)
        raw = s.transkription_json or []
        if isinstance(raw, list):
            for seg in raw:
                if not isinstance(seg, dict):
                    continue
                key = (round(float(seg.get("start", 0)), 3), round(float(seg.get("end", 0)), 3))
                if key in seg_seen:
                    continue
                seg_seen.add(key)
                text = str(seg.get("text", "")).strip()
                if text:
                    sp = seg.get("sprecher")
                    sp_txt = f" {sprecher_namen.get(sp, sp)}:" if sp else ""
                    transkript_lines.append(f"[{seg.get('start', 0):.0f}s]{sp_txt} {text}")

    transkript_text = "\n".join(transkript_lines)[:7000]
    beschreibungen_text = "\n".join(beschreibungen)[:3000]
    framing_summary = ", ".join(f"{k}: {v}" for k, v in Counter(framings).most_common())

    # Sync-/Take-Fakten (Szene/Einstellung/Take, verknüpfter Ton) — harte Metadaten, keine Deutung.
    take_zeile = ""
    if clip.take_id:
        from backend.core.database import Take as _Take
        tk = (await db.execute(select(_Take).where(_Take.id == clip.take_id))).scalar_one_or_none()
        if tk:
            teile = []
            if tk.szene is not None: teile.append(f"Szene {tk.szene}")
            if tk.plan is not None: teile.append(f"Einstellung {tk.plan}")
            if tk.prise is not None: teile.append(f"Take {tk.prise}")
            take_zeile = " · ".join(teile)
    ton_zeile = ("verknüpfter Ton (Sync)" if clip.hat_ton and clip.take_id else
                 "Kameraton" if clip.hat_ton else "kein Nutzton")

    from backend.core import einstellungen as E
    projekt_kontext = E.projekt_kontext()
    glossar = [g for g in (E.transkription().get("glossar") or []) if g]

    speaker_block = (
        f"{len(speakers)} unterschiedliche Stimmen (Diarization): " + ", ".join(
            f"{sprecher_namen.get(sp.label_auto, sp.label_auto)} ({sp.total_speaking_time:.0f}s Sprechzeit)" for sp in speakers)
        + " — die Transkriptzeilen sind mit diesen Sprechern markiert; derselbe Sprecher kann eine Figur mit Kosename UND Namen anreden."
        if speakers else "keine Diarization verfügbar"
    )
    personen_block = f"max. {personen_max} Person(en) sichtbar (Bildmodell-Zählung)" if personen_max else "keine Personen sichtbar / nicht gezählt"

    hat_dialog = bool(transkript_text.strip())
    hat_bild = bool(beschreibungen_text.strip())

    prompt = f"""Du bist ein FAKTISCHER Video-Analyst für Rohmaterial (Dailies) eines Filmdrehs. Du beschreibst NUR, was in den Belegen unten steht. Du erfindest nichts, deutest keine Gefühle und ergänzt kein Weltwissen.

SPRACHE: Alle Werte auf DEUTSCH (Belege dürfen englisch sein — übersetze sinngemäß, füge nichts hinzu). Eigennamen bleiben unverändert.

ANTWORTFORMAT: NUR gültiges JSON, exakt diese Schlüssel:
{{
  "thema": "Worum es in diesem Take geht — EIN Satz, aus Dialog und/oder Bild belegt.",
  "narration": "Was passiert, chronologisch, 2–4 Sätze. Nur Belegtes; wenn nur Bild ohne Dialog: nur das Sichtbare.",
  "visuell": "Setting, Personenzahl, Kadrage/Framing, Licht/Farben — NUR aus den Bildbeschreibungen und dem Framing.",
  "ambiance": "Stimmung NUR wenn aus Dialog belegbar (z. B. Streit, Sorge, Scherz); sonst \"nicht belegbar\".",
  "genre": "Projektart WÖRTLICH aus dem Projekt-Kontext (z. B. \"Kurzfilm\", \"Dokumentarfilm\", \"Interview\"); steht dort keine: \"unbekannt\" (NICHT raten).",
  "anwesende_personen": ["Figuren, die im Take sprechen oder sichtbar sind. Nutze die Sprecher-Markierung: spricht Sprecher A eine Figur mit Namen an, ist die angesprochene Figur ein ANDERER Sprecher. Wird ein Glossar-Name im Dialog direkt ANGESPROCHEN (Anrede: \"Babe, …\", \"Yuri, bist du da?\"), ist diese Figur ANWESEND. Namen nur aus Glossar/Projekt-Kontext; unbenannte Personen als \"Person 1\", \"Person 2\" (Anzahl = sichtbare Personen)."],
  "erwaehnte_personen": ["Namen, über die nur GESPROCHEN wird (dritte Person), die aber nicht anwesend sind — NUR wörtlich im Transkript vorkommende Namen"],
  "belege": ["2–5 kurze Zitate/Verweise aus Transkript oder Bildbeschreibung mit Zeit, die thema/narration stützen"],
  "unsicher": ["Was du NICHT sagen kannst (z. B. \"kein Dialog\", \"Personen nicht identifizierbar\")"]
}}

REGELN:
- Keine Personen, Orte, Geräte oder Handlungen, die nicht in Transkript oder Bildbeschreibungen stehen.
- Keine Prominenten, keine Zuschreibung von Absichten oder Zuständen („will“, „versucht“, „genießt“, „scheint“, „wirkt“).
- Anrede ≠ Sprecher: In „Babe, ich geh Tee machen“ ist BABE die angesprochene Person, NICHT die sprechende. Ordne Handlungen nur dann einer Figur zu, wenn das eindeutig ist; sonst „eine Person“.
- Ist das Transkript leer, sind thema/narration rein visuell und `ambiance` = "nicht belegbar".
- Wenn du dir bei einem Feld nicht sicher bist: leer/„nicht belegbar“ statt raten.

── Projekt-Kontext (vom Nutzer, gilt als Fakt) ──
{projekt_kontext or "(keiner hinterlegt)"}
Glossar (Figuren/Begriffe): {", ".join(glossar) if glossar else "(leer)"}

── Metadaten (Fakten) ──
Datei: {clip.dateiname}{(" · " + take_zeile) if take_zeile else ""}
Dauer: {clip.dauer:.0f}s · {clip.aufloesung} · Ton: {ton_zeile}
Szenen (Schnitte im Take): {len(szenen)} · Framing: {framing_summary or "unbekannt"}
Sichtbare Personen: {personen_block}
Stimmen: {speaker_block}

── Bildbeschreibungen (Bildmodell, Stichproben mit Zeit; englisch, faktisch) ──
{beschreibungen_text or "(keine)"}

── Transkript (Whisper, mit Zeit) ──
{transkript_text or "(kein Dialog)"}

Antworte JETZT nur mit dem JSON."""

    # Modellwahl: qwen2.5:14b (deutlich besser in Deutsch/JSON-Treue) > OLLAMA_MODEL (llama3).
    import os as _os
    from backend.core.vision_describe import modell_verfuegbar
    modell = _os.getenv("OLLAMA_SYNTHESE_MODEL") or ("qwen2.5:14b" if modell_verfuegbar("qwen2.5:14b") else OLLAMA_MODEL)

    try:
        async with httpx.AsyncClient(timeout=240) as client:
            r = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": modell,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "keep_alive": "3m",
                    "options": {"temperature": 0.0, "num_predict": 1000, "top_p": 0.8, "num_ctx": 8192},
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise HTTPException(502, f"Ollama nicht erreichbar : {e}")

    raw_answer = (data.get("response") or "").strip()
    try:
        synthese = _json.loads(raw_answer)
        if not isinstance(synthese, dict):
            raise ValueError("kein Objekt")
    except Exception:
        synthese = {"thema": "?", "narration": raw_answer[:1000], "visuell": "", "ambiance": "", "genre": "?",
                    "anwesende_personen": [], "erwaehnte_personen": [], "belege": [], "unsicher": ["JSON des Modells unlesbar"]}

    # ── Nachprüfung (deterministisch): Namen müssen belegt sein ──
    import re as _re
    hinweise: list[str] = []
    transkript_lc = transkript_text.lower()
    erlaubte_namen = {g.lower(): g for g in glossar}
    for m in _re.finditer(r"\b([A-ZÄÖÜ][a-zäöüß]+)\b", projekt_kontext or ""):
        erlaubte_namen.setdefault(m.group(1).lower(), m.group(1))

    def _liste(v) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in _re.split(r"[,;]", v) if x.strip()]
        return []

    anwesend_raw = _liste(synthese.get("anwesende_personen") or synthese.get("personen"))
    anwesend: list[str] = []
    for n in anwesend_raw:
        nl = n.lower()
        if _re.fullmatch(r"(person|sprecher|speaker|figur)\s*_?\d+", nl):
            anwesend.append(n); continue
        if nl in erlaubte_namen and (nl in transkript_lc or not hat_dialog):
            anwesend.append(erlaubte_namen[nl]); continue
        if nl in transkript_lc:
            anwesend.append(n); continue
        hinweise.append(f"„{n}“ als anwesend genannt, aber weder Glossar noch Transkript belegen den Namen — entfernt.")
    erwaehnt = []
    for n in _liste(synthese.get("erwaehnte_personen")):
        if n.lower() in transkript_lc:
            erwaehnt.append(n)
        else:
            hinweise.append(f"„{n}“ als erwähnt genannt, kommt im Transkript nicht vor — entfernt.")
    # Anrede-Regel (deterministisch): Ein Glossar-Name, der im Transkript als Anrede steht
    # („Babe, …“, „Yuri. Bist du da?“, alleinstehend „Yuri.“), gehört zu einer ANWESENDEN Figur.
    def _angesprochen(name: str) -> int:
        pat = _re.compile(r"(?:^|[.!?,;:]\s*)" + _re.escape(name) + r"(?=\s*(?:[,.!?;:]|$))", _re.IGNORECASE)
        return sum(1 for ln in transkript_lines if pat.search(_re.sub(r"^\[\d+s\]\s*", "", ln)))
    for n in list(erwaehnt):
        k = _angesprochen(n)
        if k >= 1 and n.lower() in erlaubte_namen:
            erwaehnt.remove(n)
            if n not in anwesend:
                anwesend.append(n)
            hinweise.append(f"„{n}“ wird im Dialog {k}× direkt angesprochen → als anwesend eingeordnet.")
    for nl, n in erlaubte_namen.items():
        if n not in anwesend and n not in erwaehnt and _angesprochen(n) >= 2:
            anwesend.append(n)
            hinweise.append(f"„{n}“ wird im Dialog {_angesprochen(n)}× direkt angesprochen → anwesend ergänzt.")
    # Platzhalter „Person n“ raus, wenn dieselbe Zahl an benannten Figuren belegt ist
    benannt = [a for a in anwesend if not _re.fullmatch(r"(person|sprecher|speaker|figur)\s*_?\d+", a.lower())]
    if benannt and personen_max and len(benannt) >= personen_max:
        anwesend = benannt
    synthese["anwesende_personen"] = anwesend
    synthese["erwaehnte_personen"] = erwaehnt
    synthese["personen"] = anwesend  # Abwärtskompatibel (altes UI-Feld)
    synthese["belege"] = _liste(synthese.get("belege"))[:6]
    unsicher = _liste(synthese.get("unsicher"))
    if not hat_dialog:
        unsicher.append("Kein Dialog transkribiert — Aussagen beruhen nur auf Bildbeschreibungen.")
    if not hat_bild:
        unsicher.append("Keine Bildbeschreibungen — Aussagen beruhen nur auf dem Dialog.")
    if not projekt_kontext:
        unsicher.append("Kein Projekt-Kontext hinterlegt (Einstellungen) — Genre/Figuren nicht zuordenbar.")
    if not speakers:
        unsicher.append("Keine Sprecher-Diarization — Zahl der Sprechenden ist nicht belegt.")
    synthese["unsicher"] = unsicher
    synthese["hinweise"] = hinweise
    synthese["belege_zahl"] = {"dialog_segmente": len(transkript_lines), "bildbeschreibungen": len(beschreibungen), "sprecher": len(speakers)}
    if not projekt_kontext and str(synthese.get("genre") or "").strip().lower() not in ("", "unbekannt", "?"):
        hinweise.append(f"Genre „{synthese.get('genre')}“ ist geraten (kein Projekt-Kontext) — auf „unbekannt“ gesetzt.")
        synthese["genre"] = "unbekannt"

    synthese["generated_at"] = _dt.datetime.utcnow().isoformat() + "Z"
    synthese["model"] = modell

    clip.synthese_json = synthese
    await db.commit()

    return {"clip_id": str(clip.id), "cached": False, "synthese": synthese}


# ─── Neu transkribieren ─────────────────────────────────

@router.post("/{clip_id}/transkribieren")
async def clip_neu_transkribieren(clip_id: str, db: AsyncSession = Depends(get_db)):
    """Nur die Tonspur neu durch Whisper schicken (aktuelle Einstellungen: Sprache/Glossar/Modell/Kanal).
    Proxy, Szenen, Embeddings bleiben; die Szenen-Transkripte werden ersetzt."""
    from backend.workers.ingest import transkribieren
    clip = (await db.execute(select(Clip).where(Clip.id == clip_id))).scalar_one_or_none()
    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")
    job = Job(id=uuid.uuid4(), typ="transkription", clip_id=clip.id, status="wartend", fortschritt=0, nachricht="Neu-Transkription wartet…")
    db.add(job)
    await db.commit()
    task = transkribieren.delay(str(clip.id), str(job.id))
    job.celery_task_id = task.id
    await db.commit()
    return {"job_id": str(job.id)}


@router.post("/{clip_id}/neu-analysieren")
async def clip_neu_analysieren(clip_id: str, db: AsyncSession = Depends(get_db)):
    """Komplette Analyse erneut (Ton, Whisper, Szenen, Bildanalyse mit Stichproben, Embeddings).
    Alte Szenen/Sprecher werden ersetzt (keine Duplikate), Proxy wird wiederverwendet, Bericht-Cache geleert."""
    from backend.workers.ingest import ingestion_pipeline
    clip = (await db.execute(select(Clip).where(Clip.id == clip_id))).scalar_one_or_none()
    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")
    clip.status = "hochgeladen"
    clip.synthese_json = None
    job = Job(id=uuid.uuid4(), typ="ingestion", clip_id=clip.id, status="wartend", fortschritt=0, nachricht="Neu-Analyse wartet…")
    db.add(job)
    await db.commit()
    task = ingestion_pipeline.delay(str(clip.id), str(job.id))
    job.celery_task_id = task.id
    await db.commit()
    return {"job_id": str(job.id)}


# ─── Clip löschen ────────────────────────────────────────

@router.delete("/{clip_id}")
async def clip_loeschen(clip_id: str, db: AsyncSession = Depends(get_db)):
    """Clip und zugehörige Dateien löschen."""
    result = await db.execute(
        select(Clip).where(Clip.id == clip_id)
        .options(selectinload(Clip.szenen), selectinload(Clip.jobs))
    )
    clip = result.scalar_one_or_none()

    if not clip:
        raise HTTPException(404, "Clip nicht gefunden.")

    # Datei löschen — NUR Upload-Kopien. Per Referenz importierte Originale
    # (Clip mit take_id / Pfad außerhalb von UPLOAD_DIR) werden NIE angefasst.
    pfad = Path(clip.dateipfad)
    if pfad.exists() and not clip.take_id and ist_upload_datei(clip.dateipfad):
        pfad.unlink()

    # Thumbnails löschen
    thumbs_dir = Path(f"temp/thumbs_{clip_id}")
    if thumbs_dir.exists():
        shutil.rmtree(thumbs_dir)

    await db.delete(clip)
    await db.commit()

    return {"nachricht": f"Clip '{clip.dateiname}' gelöscht."}
