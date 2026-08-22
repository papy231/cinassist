"""
CinAssist — FastAPI Hauptanwendung

Start: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

from contextlib import asynccontextmanager
import mimetypes
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.core.config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, PROXY_DIR
from backend.core.database import init_db
from backend.api.clips import router as clips_router
from backend.api.timelines import router as timelines_router
from backend.api.ai import router as ai_router
# DEPRECATED : chat.py = ancien flow "guided cutting" (katalog + [VORSCHLAG:] tag),
# abgelöst durch agent.py, ReAct samt Werkzeugen. Seit dem Umbau ruft die Oberfläche es nicht mehr auf
# SSE-Streaming; abgeschaltet, um zwei parallele Assistenzrollen mit
# abweichenden Systemanweisungen zu vermeiden. Datei bleibt als Beleg erhalten.
# from backend.api.chat import router as chat_router
from backend.api.websocket import router as ws_router
from backend.api.export import router as export_router
from backend.api.search import router as search_router
from backend.api.agent import router as agent_router
from backend.api.sync import router as sync_router
from backend.api.ordner import router as ordner_router
from backend.api.skript import router as skript_router
from backend.api.projekt import router as projekt_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Datenbank-Tabellen beim Start erstellen."""
    await init_db()
    yield


app = FastAPI(
    title="CinAssist API",
    description="KI-gestützte Videoschnitt-Plattform — 100% lokal",
    version="0.1.0",
    lifespan=lifespan,
)

# ─── CORS (aus env CORS_ORIGINS, Default: localhost:3000/3001/3003) ──────
# Tailscale-URL & Prod-Domains via env CORS_ORIGINS="url1,url2,..."
from backend.core.config import CORS_ORIGINS
_extra_origin = "https://macmini.tailef3707.ts.net:3003"
_origins = list(dict.fromkeys(CORS_ORIGINS + [_extra_origin]))  # dedup, keep order
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Statische Dateien mit HTTP Range-Support (nötig für video seek) ─────
# Starlette 0.38 StaticFiles unterstützt keine Range-Header → seek über den
# vorgeladenen Buffer hinaus schlägt still fehl (Frontend bleibt bei time=0).
# Custom Handler unten liefert 206 Partial Content bei Range-Anfragen.

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 1024 * 512  # 512 KB pro Chunk beim Streaming

def _serve_range(base_dir: Path, subpath: str, request: Request) -> StreamingResponse:
    file_path = (base_dir / subpath).resolve()
    # Path traversal-Schutz : darf nicht ausserhalb base_dir liegen
    try:
        file_path.relative_to(base_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    file_size = file_path.stat().st_size
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    range_header = request.headers.get("range") or request.headers.get("Range")

    if range_header:
        match = _RANGE_RE.match(range_header.strip())
        if not match:
            raise HTTPException(status_code=416, detail="Invalid Range")
        start_str, end_str = match.group(1), match.group(2)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable",
                                headers={"Content-Range": f"bytes */{file_size}"})
        length = end - start + 1

        def iter_file():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "public, max-age=3600",
        }
        return StreamingResponse(iter_file(), status_code=206,
                                 media_type=content_type, headers=headers)

    # Kein Range → ganze Datei, aber trotzdem mit Accept-Ranges damit der Browser weiss dass seek unterstützt wird
    def iter_full():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "public, max-age=3600",
    }
    return StreamingResponse(iter_full(), media_type=content_type, headers=headers)


@app.get("/proxies/{subpath:path}")
def serve_proxy(subpath: str, request: Request):
    return _serve_range(PROXY_DIR, subpath, request)


@app.get("/uploads/{subpath:path}")
def serve_upload(subpath: str, request: Request):
    return _serve_range(UPLOAD_DIR, subpath, request)


@app.get("/outputs/{subpath:path}")
def serve_output(subpath: str, request: Request):
    return _serve_range(OUTPUT_DIR, subpath, request)


# /temp bleibt beim StaticFiles-Mount (kleine Dateien, kein Video)
app.mount("/temp", StaticFiles(directory=str(TEMP_DIR)), name="temp")

# ─── Router ─────────────────────────────────────────────
app.include_router(clips_router)
app.include_router(timelines_router)
app.include_router(ai_router)
# app.include_router(chat_router)  # abgeschaltet, siehe Hinweis beim Import
app.include_router(ws_router)
app.include_router(export_router)
app.include_router(search_router)
app.include_router(agent_router)
app.include_router(sync_router)
app.include_router(ordner_router)
app.include_router(skript_router)
app.include_router(projekt_router)


@app.get("/")
async def root():
    return {
        "name": "CinAssist API",
        "version": "0.1.0",
        "status": "aktiv",
        "endpunkte": {
            "clips_hochladen": "POST /api/clips/upload",
            "clips_auflisten": "GET /api/clips",
            "clip_analyse": "GET /api/clips/{clip_id}/analyse",
            "job_status": "ws://localhost:8000/ws/jobs/{job_id}",
        },
    }


@app.get("/api/system/transkription")
def transkription_einstellungen_lesen():
    from backend.core import einstellungen as E
    return E.transkription()


@app.get("/api/system/projekt")
def projekt_einstellungen_lesen():
    from backend.core import einstellungen as E
    return E.projekt()


@app.put("/api/system/projekt")
async def projekt_einstellungen_setzen(request: Request):
    from backend.core import einstellungen as E
    body = await request.json()
    kontext = str(body.get("kontext") or "")[:2000]
    neu = {"kontext": kontext}
    if "max_sprecher" in body:
        v = body.get("max_sprecher")
        try:
            neu["max_sprecher"] = int(v) if v not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "max_sprecher: Zahl oder leer")
        if neu["max_sprecher"] is not None and not (1 <= neu["max_sprecher"] <= 20):
            raise HTTPException(400, "max_sprecher: 1–20")
    E.speichere({"projekt": neu})
    return E.projekt()


@app.put("/api/system/transkription")
async def transkription_einstellungen_setzen(request: Request):
    from backend.core import einstellungen as E
    body = await request.json()
    erlaubt = {k: body[k] for k in ("sprache", "glossar", "modell", "kanal") if k in body}
    if "glossar" in erlaubt and isinstance(erlaubt["glossar"], str):
        erlaubt["glossar"] = [g.strip() for g in re.split(r"[,\n;]+", erlaubt["glossar"]) if g.strip()]
    if erlaubt.get("modell") not in (None, "turbo", "qualitaet"):
        raise HTTPException(400, "modell: turbo | qualitaet")
    if erlaubt.get("kanal") not in (None, "sprachreichster", "record"):
        raise HTTPException(400, "kanal: sprachreichster | record")
    E.speichere({"transkription": erlaubt})
    return E.transkription()


@app.get("/api/system/config")
async def system_config():
    """Read-only Systemkonfiguration (Modelle, Thresholds, Provider-Status).

    Wird vom /settings-Frontend genutzt. Bearbeitung erfolgt via env vars +
    Neustart, nicht via API (Sicherheit + Konsistenz).
    """
    from backend.core.config import (
        WHISPER_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL, CLIP_MODEL,
        SCENE_THRESHOLD, AUDIO_SAMPLE_RATE, CLIP_EMBEDDING_DIM,
        TIMEZONE, CORS_ORIGINS,
        CLAUDE_API_KEY, CLAUDE_MODEL,
        OPENAI_API_KEY, OPENAI_MODEL,
        GEMINI_API_KEY, GEMINI_MODEL,
        FFMPEG_BIN, FFPROBE_BIN,
    )
    import os

    # Prüfe pyannote HF-Token (für Diarization)
    hf_token_set = bool(
        os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("HF_TOKEN")
        or (Path.home() / ".openclaw/workspace/.secrets/huggingface.json").exists()
        or (Path.home() / ".cache/huggingface/token").exists()
    )

    return {
        "agent": {
            "model": "qwen2.5:14b",
            "max_iterations": 12,
            "temperature": 0.2,
        },
        "whisper": {
            "model": WHISPER_MODEL,
            "sample_rate": AUDIO_SAMPLE_RATE,
            "language": "auto (None)",
        },
        "ollama": {
            "base_url": OLLAMA_BASE_URL,
            "description_model": OLLAMA_MODEL,
            "vision_model": "moondream:latest",
            "agent_model": "qwen2.5:14b",
        },
        "clip_embedding": {
            "model": CLIP_MODEL,
            "dimension": CLIP_EMBEDDING_DIM,
        },
        "scene_detection": {
            "threshold": SCENE_THRESHOLD,
            "backend": "PySceneDetect ContentDetector",
        },
        "diarization": {
            "model": "pyannote/speaker-diarization-3.1",
            "hf_token_configured": hf_token_set,
            "min_speaker_time_s": 3.0,
        },
        "cloud_providers": {
            "claude": {"available": bool(CLAUDE_API_KEY), "model": CLAUDE_MODEL},
            "openai": {"available": bool(OPENAI_API_KEY), "model": OPENAI_MODEL},
            "gemini": {"available": bool(GEMINI_API_KEY), "model": GEMINI_MODEL},
        },
        "system": {
            "timezone": TIMEZONE,
            "cors_origins": CORS_ORIGINS,
            "ffmpeg": FFMPEG_BIN,
            "ffprobe": FFPROBE_BIN,
        },
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
