"""
CinAssist — Export API
POST /api/export       → Timeline als MP4 exportieren (FFmpeg + xfade, Celery-Job)
POST /api/export/open-in → FCPXML/EDL schreiben + nativen NLE öffnen via `open -a`
"""
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db, Job
from backend.workers.export import export_video_task

logger = logging.getLogger("cinassist.export")
router = APIRouter(prefix="/api", tags=["Export"])


# ─── Pydantic-Modelle ────────────────────────────────────

class TransitionDef(BaseModel):
    type: str = "dissolve"
    dauer: float = 0.5


class SegmentExport(BaseModel):
    id: str
    clip_id: str
    track: str
    start: float
    dauer: float
    mediaStart: float = 0.0
    transition: TransitionDef | None = None


class ExportRequest(BaseModel):
    segments: list[SegmentExport]
    resolution: str = "1920x1080"
    name: str = "Export"


# ─── Endpoint ────────────────────────────────────────────

@router.post("/export")
async def export_timeline(req: ExportRequest, db: AsyncSession = Depends(get_db)):
    """
    Startet einen asynchronen FFmpeg-Export-Job.
    Gibt job_id zurück — WebSocket-Tracking über /ws/jobs/{job_id}.
    """
    if not req.segments:
        raise HTTPException(400, "Keine Segmente angegeben")

    # Video-Segmente vorhanden?
    v_segs = [s for s in req.segments if s.track.startswith("v")]
    if not v_segs:
        raise HTTPException(400, "Keine Videosegmente in der Timeline")

    # Job in DB anlegen
    job = Job(
        id=uuid.uuid4(),
        typ="export",
        status="wartend",
        fortschritt=0,
        nachricht=f"Export wartet… ({len(v_segs)} Videosegmente)",
    )
    db.add(job)
    await db.commit()

    job_id = str(job.id)

    # Celery-Task starten
    export_video_task.delay(
        job_id,
        {
            "segments": [s.dict() for s in req.segments],
            "resolution": req.resolution,
            "name": req.name,
        },
    )

    return {
        "job_id": job_id,
        "nachricht": f"Export gestartet — {len(v_segs)} Videosegmente",
    }


# ─── "Senden an..." NLE ───────────────────────────────────
# Schreibt die FCPXML- / EDL-Datei in ein Sammel-Verzeichnis und
# öffnet sie direkt mit der gewählten Software via macOS `open -a`.
# DaVinci Resolve und Premiere Pro können beide FCPXML importieren;
# Final Cut Pro nutzt es nativ.

class SendToAppRequest(BaseModel):
    app: Literal["davinci", "premiere", "fcp"] = Field(
        ..., description="Ziel-NLE: davinci / premiere / fcp"
    )
    fcpxml: str = Field(..., description="FCPXML-Inhalt (vom Frontend gebaut)")
    name: str = Field("CinAssist_Timeline", description="Datei-Basisname")


# Mapping von App-Slug → macOS-Bundle-Namen (für `open -a`)
APP_BUNDLE_NAMES: dict[str, list[str]] = {
    # mehrere Kandidaten: macOS findet den passenden installierten
    "davinci":  ["DaVinci Resolve", "DaVinci Resolve Studio"],
    "premiere": ["Adobe Premiere Pro 2024", "Adobe Premiere Pro 2025", "Adobe Premiere Pro 2023", "Adobe Premiere Pro"],
    "fcp":      ["Final Cut Pro"],
}


def _app_installiert(app: str) -> bool:
    """Prüft ob die App auf macOS installiert ist (über `osascript -e id of app`)."""
    try:
        r = subprocess.run(
            ["osascript", "-e", f'id of application "{app}"'],
            capture_output=True, text=True, timeout=3.0,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def _versuche_open(app_slug: str, datei: Path) -> tuple[bool, str]:
    """
    Öffnet die FCPXML-Datei mit der gewählten NLE.

    Strategie:
      1. Prüfen welcher App-Bundle-Name auf dem System verfügbar ist
      2. App OHNE Datei starten (damit sie nicht das FCPXML mit ihrem
         eigenen 'unbekannt'-Dialog beantwortet)
      3. Parallel `open -R` ausführen: Finder öffnet den Ordner und
         markiert die Datei → der User sieht sie und kann sie reinziehen

    Wir liefern (erfolg, geöffnete_app_oder_fehler).
    """
    kandidaten = APP_BUNDLE_NAMES.get(app_slug, [])
    fehler: list[str] = []

    # Welcher Bundle-Name ist tatsächlich installiert?
    installiert: str | None = None
    for app in kandidaten:
        if _app_installiert(app):
            installiert = app
            break

    if not installiert:
        return False, f"Keine der gesuchten Apps ist installiert: {', '.join(kandidaten)}"

    # App starten (ohne Datei) — die App öffnet sich mit einem leeren Projekt
    try:
        subprocess.run(["open", "-a", installiert], capture_output=True, text=True, timeout=5.0)
        logger.info(f"NLE gestartet: {installiert}")
    except Exception as exc:
        fehler.append(f"{installiert}-start: {exc}")

    # Finder mit selektiertem File öffnen, damit der User die FCPXML SIEHT
    # und per Drag-and-Drop in die NLE ziehen kann. Dies funktioniert
    # zuverlässig auch wenn die NLE die FCPXML nicht beim Launch importiert.
    try:
        subprocess.run(["open", "-R", str(datei)], capture_output=True, text=True, timeout=3.0)
        logger.info(f"Finder öffnet: {datei}")
    except Exception as exc:
        fehler.append(f"finder: {exc}")

    return True, installiert


def _davinci_laeuft() -> bool:
    """Prüft, ob ein DaVinci-Resolve-Prozess aktiv ist."""
    try:
        r = subprocess.run(["pgrep", "-f", "DaVinci Resolve"], capture_output=True, timeout=3.0)
        return r.returncode == 0
    except Exception:
        return False


def _davinci_direkt_import(datei: Path, timeline_name: str) -> tuple[bool, str]:
    """
    Versucht, die FCPXML DIREKT in DaVinci Resolve zu importieren — über die
    Resolve-Scripting-API (backend/tools/davinci_import.py). Bei Erfolg
    erscheint die Timeline ohne manuellen File→Import-Schritt.

    Voraussetzung: Resolve läuft + 'External scripting' ist aktiviert.
    Liefert (erfolg, nachricht).
    """
    import sys

    # DaVinci starten, falls nicht aktiv (Scripting braucht laufende Instanz)
    if not _davinci_laeuft():
        try:
            subprocess.run(["open", "-a", "DaVinci Resolve"], capture_output=True, timeout=5.0)
        except Exception:
            pass
        # Resolve braucht Zeit zum Hochfahren — warten, dann probieren
        time.sleep(12.0)

    from backend.core.config import UPLOAD_DIR

    try:
        r = subprocess.run(
            [sys.executable, "-m", "backend.tools.davinci_import",
             str(datei), str(UPLOAD_DIR) + "/", timeline_name],
            capture_output=True, text=True, timeout=60.0,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
        if r.returncode == 0:
            logger.info(f"DaVinci-Direktimport erfolgreich: {datei.name}")
            return True, "Timeline direkt in DaVinci Resolve importiert und geöffnet."
        logger.warning(f"DaVinci-Direktimport fehlgeschlagen: {r.stderr.strip()}")
        return False, r.stderr.strip() or "Import-Skript-Fehler"
    except subprocess.TimeoutExpired:
        return False, "DaVinci-Import Timeout (>60s)"
    except Exception as exc:
        return False, f"Import-Fehler: {exc}"


@router.post("/export/open-in")
async def export_open_in(body: SendToAppRequest):
    """
    Schreibt die FCPXML in ~/Documents/CinAssist_Exports/.

    Für DaVinci Resolve: versucht den DIREKTEN Timeline-Import via
    Scripting-API — die Timeline erscheint ohne manuellen Schritt. Schlägt
    das fehl (Scripting deaktiviert / Resolve nicht bereit), wird auf den
    Finder-Reveal-Modus zurückgefallen.

    Für Premiere / Final Cut: App starten + Finder mit der Datei öffnen
    (diese NLEs haben kein zuverlässiges Headless-Import-Interface).
    """
    ziel_ordner = Path.home() / "Documents" / "CinAssist_Exports"
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in body.name if c.isalnum() or c in "_-") or "timeline"
    datei = ziel_ordner / f"{safe_name}_{timestamp}.fcpxml"
    datei.write_text(body.fcpxml, encoding="utf-8")
    logger.info(f"FCPXML geschrieben: {datei}  ({len(body.fcpxml)} bytes)")

    # ── DaVinci: Direktimport via Scripting-API versuchen ──
    if body.app == "davinci":
        # Eindeutiger Timeline-Name (Zeitstempel) — verhindert Kollision
        ok, msg = _davinci_direkt_import(datei, f"CinAssist {timestamp}")
        if ok:
            return {
                "status": "importiert",
                "app": "DaVinci Resolve",
                "datei": str(datei),
                "groesse_bytes": len(body.fcpxml),
                "nachricht": msg,
            }
        # Fallback: Finder-Reveal — der User importiert manuell
        logger.info(f"Direktimport nicht möglich ({msg}) — Fallback auf Finder-Reveal")
        try:
            subprocess.run(["open", "-a", "DaVinci Resolve"], capture_output=True, timeout=5.0)
            subprocess.run(["open", "-R", str(datei)], capture_output=True, timeout=3.0)
        except Exception:
            pass
        return {
            "status": "geöffnet",
            "app": "DaVinci Resolve",
            "datei": str(datei),
            "groesse_bytes": len(body.fcpxml),
            "nachricht": (
                f"Direktimport nicht möglich ({msg}). DaVinci wurde gestartet und "
                f"der Finder zeigt {datei.name} — ziehe die Datei in DaVinci oder "
                f"nutze File → Import → Timeline."
            ),
        }

    # ── Premiere / Final Cut: App + Finder ──
    ok, info = _versuche_open(body.app, datei)
    if not ok:
        raise HTTPException(
            502,
            f"FCPXML wurde gespeichert ({datei.name}), aber {body.app} konnte nicht "
            f"gestartet werden: {info}. Du kannst die Datei manuell öffnen.",
        )

    return {
        "status": "geöffnet",
        "app": info,
        "datei": str(datei),
        "groesse_bytes": len(body.fcpxml),
        "nachricht": (
            f"{info} wurde gestartet und der Finder zeigt {datei.name}.  "
            f"Ziehe die Datei in die NLE oder verwende dort File → Import → Timeline."
        ),
    }
