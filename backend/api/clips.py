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
from backend.workers.ingest import ingestion_pipeline

router = APIRouter(prefix="/api/clips", tags=["Clips"])

# Erlaubte Dateiformate
ERLAUBTE_ENDUNGEN = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_DATEIGROESSE = 5 * 1024 * 1024 * 1024  # 5 GB


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

    # Clip in DB anlegen
    clip = Clip(
        id=clip_id,
        dateiname=dateiname,
        dateipfad=str(ziel_pfad),
        quelle=quelle,
        dateigroesse=dateigroesse,
        status="hochgeladen",
    )
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
    result = await db.execute(
        select(Clip).order_by(Clip.erstellt_am.desc())
    )
    clips = result.scalars().all()

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
            "video_url": f"/uploads/{Path(clip.dateipfad).name}" if clip.dateipfad else None,
            "proxy_url": (
                f"/proxies/{Path(clip.dateipfad).stem}_proxy.mp4"
                if clip.dateipfad and _nonempty(PROXY_DIR / f"{Path(clip.dateipfad).stem}_proxy.mp4")
                else None
            ),
            "waveform_url": (
                f"/proxies/{Path(clip.dateipfad).stem}_wf.png"
                if clip.dateipfad and (PROXY_DIR / f"{Path(clip.dateipfad).stem}_wf.png").exists()
                else None
            ),
            "strip_url": (
                f"/proxies/{Path(clip.dateipfad).stem}_strip.jpg"
                if clip.dateipfad and (PROXY_DIR / f"{Path(clip.dateipfad).stem}_strip.jpg").exists()
                else None
            ),
            "dateipfad": clip.dateipfad,
        }
        for clip in clips
    ]


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
        "video_url": f"/uploads/{Path(clip.dateipfad).name}" if clip.dateipfad else None,
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
                "hat_embedding": s.clip_embedding is not None and s.clip_embedding != [0.0] * 512,
                "thumbnail_pfad": s.thumbnail_pfad,
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
        proxy_pfad = PROXY_DIR / f"{Path(clip.dateipfad).stem}_proxy.mp4"
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
    if hat_transkription or szenen:
        history["audio"] = {
            "format": "WAV PCM 16-bit (temporär, nach Transkription gelöscht)",
            "sample_rate": 16000,
            "channels": 1,
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

    # Datei löschen
    pfad = Path(clip.dateipfad)
    if pfad.exists():
        pfad.unlink()

    # Thumbnails löschen
    thumbs_dir = Path(f"temp/thumbs_{clip_id}")
    if thumbs_dir.exists():
        shutil.rmtree(thumbs_dir)

    await db.delete(clip)
    await db.commit()

    return {"nachricht": f"Clip '{clip.dateiname}' gelöscht."}
