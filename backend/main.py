"""
CinAssist — FastAPI Hauptanwendung

Start: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.core.config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, PROXY_DIR
from backend.core.database import init_db
from backend.api.clips import router as clips_router
from backend.api.timelines import router as timelines_router
from backend.api.ai import router as ai_router
from backend.api.chat import router as chat_router
from backend.api.websocket import router as ws_router
from backend.api.export import router as export_router


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

# ─── CORS (Frontend auf localhost:3000) ──────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Statische Dateien ──────────────────────────────────
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")
app.mount("/temp", StaticFiles(directory=str(TEMP_DIR)), name="temp")
app.mount("/proxies", StaticFiles(directory=str(PROXY_DIR)), name="proxies")

# ─── Router ─────────────────────────────────────────────
app.include_router(clips_router)
app.include_router(timelines_router)
app.include_router(ai_router)
app.include_router(chat_router)
app.include_router(ws_router)
app.include_router(export_router)


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


@app.get("/health")
async def health():
    return {"status": "ok"}
