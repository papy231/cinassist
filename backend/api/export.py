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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
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
    app: Literal["davinci", "premiere", "fcp", "avid"] = Field(
        ..., description="Ziel-NLE: davinci / premiere / fcp / avid"
    )
    segments: list[SegmentExport] = Field(
        ..., description="Timeline-Segmente (wie beim /api/export-Endpoint)"
    )
    name: str = Field("CinAssist_Timeline", description="Datei-Basisname")
    fps: float = Field(30.0, description="Framerate für den OTIO-Zeitbezug")


# Mapping von App-Slug → macOS-Bundle-Namen (für `open -a`)
APP_BUNDLE_NAMES: dict[str, list[str]] = {
    # mehrere Kandidaten: macOS findet den passenden installierten
    "davinci":  ["DaVinci Resolve", "DaVinci Resolve Studio"],
    "premiere": ["Adobe Premiere Pro 2024", "Adobe Premiere Pro 2025", "Adobe Premiere Pro 2023", "Adobe Premiere Pro"],
    "fcp":      ["Final Cut Pro"],
    "avid":     ["Media Composer", "Avid Media Composer"],
}

# Format d'échange pour chaque NLE.
# DaVinci/Premiere/FCP → FCPXML (natif ou via adapter).
# AVID Media Composer → EDL (CMX3600 — cuts-only mais universel).
APP_FORMAT: dict[str, Literal["fcpxml", "edl"]] = {
    "davinci":  "fcpxml",
    "premiere": "fcpxml",
    "fcp":      "fcpxml",
    "avid":     "edl",
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


_LOCAL_HOSTNAMES = ("localhost", "127.0.0.1", "[::1]", "::1")


def _client_ist_remote(request: Request) -> bool:
    """
    True wenn der Browser NICHT auf dem Backend-Host läuft — dann macht
    Direct-Import / Finder-Reveal keinen Sinn (öffnet Resolve auf dem
    falschen Rechner). Stattdessen liefern wir die FCPXML als Download.

    Wir prüfen den Origin/Host-Header (nicht request.client.host), weil
    Tailscale serve / Caddy loopback-Adresse zeigen selbst wenn der Browser
    remote ist.
    """
    origin = (request.headers.get("origin") or "").lower()
    if origin:
        # origin = "https://mac-mini-openclaw.tailef3707.ts.net:3003"
        # → hostname zwischen // und : / /
        host = origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    else:
        host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    return bool(host) and host not in _LOCAL_HOSTNAMES


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


@router.get("/export/download/{filename}")
async def export_download(filename: str):
    """
    Sert die zuvor generierte FCPXML/EDL an den Browser (für Remote-Clients,
    die die Datei lokal in ihre NLE ziehen wollen). Path-Traversal-safe:
    resolved Pfad MUSS unter EXPORT_DIR liegen.
    """
    from backend.core.otio_export import EXPORT_DIR

    ziel = (EXPORT_DIR / filename).resolve()
    try:
        ziel.relative_to(EXPORT_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Ungültiger Dateiname")
    if not ziel.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(
        path=str(ziel),
        filename=ziel.name,
        media_type="application/octet-stream",
    )


@router.post("/export/open-in")
async def export_open_in(body: SendToAppRequest, request: Request):
    """
    Baut FCPXML (DaVinci/Premiere/FCP) oder EDL (AVID) aus den Timeline-Segmenten
    und öffnet die Ziel-NLE direkt.

    Für DaVinci Resolve: versucht den DIREKTEN Timeline-Import via
    Scripting-API — die Timeline erscheint ohne manuellen Schritt. Schlägt
    das fehl (Scripting deaktiviert / Resolve nicht bereit), wird auf den
    Finder-Reveal-Modus zurückgefallen.

    Für Premiere / Final Cut / AVID: App starten + Finder mit der Datei öffnen
    (diese NLEs haben kein zuverlässiges Headless-Import-Interface).
    """
    from backend.core.otio_export import export_to_file
    from backend.workers.export import _resolve_clips

    if not body.segments:
        raise HTTPException(400, "Keine Segmente angegeben")
    v_segs = [s for s in body.segments if s.track.startswith("v")]
    if not v_segs:
        raise HTTPException(400, "Keine Videosegmente in der Timeline")

    # clip_id → absoluter Dateipfad (aus DB auflösen)
    file_map = _resolve_clips([s.dict() for s in body.segments])
    otio_segments: list[dict] = []
    for s in v_segs:
        fp = file_map.get(s.clip_id)
        if not fp:
            raise HTTPException(404, f"Clip nicht gefunden: {s.clip_id}")
        otio_segments.append({
            "clip_path": fp,
            "clip_name": Path(fp).stem,
            "media_start": s.mediaStart,
            "duration": s.dauer,
            "track": s.track.lower(),
        })

    fmt = APP_FORMAT[body.app]
    try:
        res = export_to_file(otio_segments, format=fmt, name=body.name, fps=body.fps)
    except Exception as exc:
        logger.exception(f"OTIO-Export fehlgeschlagen ({fmt}): {exc}")
        raise HTTPException(500, f"{fmt.upper()}-Datei konnte nicht erzeugt werden: {exc}")

    datei = Path(res["path"])
    logger.info(f"{fmt.upper()} geschrieben: {datei}  ({res['size_bytes']} bytes)")

    # ── Remote-Client: FCPXML als Download liefern ──
    # Der Browser läuft nicht auf dem Backend-Host (z.B. MacBook greift
    # auf Mac-mini-Backend zu). Direct-Import / `open -a` würde Resolve auf
    # der falschen Maschine starten — stattdessen laden wir die Datei runter.
    if _client_ist_remote(request):
        return {
            "status": "download",
            "app": APP_BUNDLE_NAMES.get(body.app, [body.app])[0],
            "datei": str(datei),
            "download_url": f"/api/export/download/{datei.name}",
            "groesse_bytes": res["size_bytes"],
            "nachricht": (
                f"{datei.name} wird heruntergeladen — ziehe die Datei in deiner "
                f"lokalen NLE per Drag-and-Drop oder öffne sie mit File → Import → Timeline."
            ),
        }

    # ── DaVinci: Direktimport via Scripting-API versuchen ──
    if body.app == "davinci":
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        ok, msg = _davinci_direkt_import(datei, f"CinAssist {timestamp}")
        if ok:
            return {
                "status": "importiert",
                "app": "DaVinci Resolve",
                "datei": str(datei),
                "groesse_bytes": res["size_bytes"],
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
            "groesse_bytes": res["size_bytes"],
            "nachricht": (
                f"Direktimport nicht möglich ({msg}). DaVinci wurde gestartet und "
                f"der Finder zeigt {datei.name} — ziehe die Datei in DaVinci oder "
                f"nutze File → Import → Timeline."
            ),
        }

    # ── Premiere / Final Cut / AVID: App + Finder ──
    ok, info = _versuche_open(body.app, datei)
    if not ok:
        raise HTTPException(
            502,
            f"{fmt.upper()} wurde gespeichert ({datei.name}), aber {body.app} konnte nicht "
            f"gestartet werden: {info}. Du kannst die Datei manuell öffnen.",
        )

    return {
        "status": "geöffnet",
        "app": info,
        "datei": str(datei),
        "groesse_bytes": res["size_bytes"],
        "nachricht": (
            f"{info} wurde gestartet und der Finder zeigt {datei.name}.  "
            f"Ziehe die Datei in die NLE oder verwende dort File → Import → Timeline."
        ),
    }
