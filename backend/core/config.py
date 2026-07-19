"""CinAssist — Konfiguration (100% lokal, kein Cloud)"""

import os
from pathlib import Path

# ─── Pfade ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
TEMP_DIR = BASE_DIR / "temp"
PROXY_DIR = BASE_DIR / "proxies"

for d in (UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, PROXY_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─── Datenbank ───────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://cinassist:cinassist@localhost:5432/cinassist",
)
DATABASE_URL_SYNC = DATABASE_URL.replace("+asyncpg", "")

# ─── Redis / Celery ─────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL

# ─── KI-Modelle (lokal) ─────────────────────────────────
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
CLIP_MODEL = os.getenv("CLIP_MODEL", "ViT-B/32")

# ─── Cloud LLM Provider (optionale API-Keys) ─────────────
# Anthropic Claude (z.B. claude-3-5-sonnet-20241022, claude-3-opus-20240229)
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
CLAUDE_MODEL   = os.getenv("CLAUDE_MODEL",   "claude-3-5-sonnet-20241022")

# OpenAI GPT-4 (z.B. gpt-4o, gpt-4o-mini, o1-preview)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL",   "gpt-4o")

# Google Gemini (z.B. gemini-1.5-pro, gemini-1.5-flash)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL",   "gemini-1.5-pro")

# ─── FFmpeg ──────────────────────────────────────────────
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")

# ─── WebSocket ───────────────────────────────────────────
WS_PING_INTERVAL = 20
WS_PING_TIMEOUT = 60

# ─── Analyse-Parameter ──────────────────────────────────
AUDIO_SAMPLE_RATE = 16000
SCENE_THRESHOLD = 27.0  # PySceneDetect ContentDetector
CLIP_EMBEDDING_DIM = 512

# ─── Zeitzone (Celery Beat, Logs) ──────────────────────
TIMEZONE = os.getenv("TZ", "Europe/Berlin")

# ─── CORS erlaubte Origins ─────────────────────────────
_default_origins = "http://localhost:3003,http://localhost:3000,http://127.0.0.1:3003"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]
