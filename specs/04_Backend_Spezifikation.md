# 04 — Backend-Spezifikation

> Technologie: Python 3.12, FastAPI 0.115, SQLAlchemy 2.0 (async), Celery 5.4, Redis 5.

## 4.1 Modulstruktur (Soll)

```
backend/
├── main.py              # FastAPI-App, Middlewares (CORS), StaticFiles, Router-Registrierung
├── core/
│   ├── config.py        # Umgebungsvariablen, Pfade, Modell-IDs, Schwellen
│   ├── database.py      # SQLAlchemy-Modelle + async/sync Engines + init_db()
│   └── celery_app.py    # Celery-Konfiguration (Broker/Backend = Redis)
├── api/
│   ├── clips.py         # Upload, Liste, Detail, Analyse, Pipeline-Bericht, Löschen
│   ├── ai.py            # KI-Schnitt + Atlas + Multicam + Reorganize + Providers
│   ├── chat.py          # Chat-Assistent über das Projektmaterial
│   ├── timelines.py     # CRUD Timelines
│   ├── export.py        # Export-Job + Übergabe an externes NLE
│   └── websocket.py     # Echtzeit Job-Fortschritt (Redis Pub/Sub → WS)
├── workers/
│   ├── ingest.py        # Phase-2-Pipeline (Celery Task)
│   └── export.py        # Phase-4-Export (Celery Task)
└── tools/               # Einmal-/Wartungsskripte (Backfill, Prompt-Embeddings, DaVinci-Import)
```

> **Aufräum-Hinweis (Soll):** `backend/api/ai_old.py` ist eine Altversion und SOLL vor der
> Abgabe entfernt oder klar als deprecated markiert werden, um Verwirrung im Code-Review
> zu vermeiden.

## 4.2 Konfiguration (`core/config.py`)

Alle Einstellungen MÜSSEN per Umgebungsvariable überschreibbar sein (`NFR-16`).

| Variable | Default | Zweck |
|----------|---------|-------|
| `DATABASE_URL` | `postgresql+asyncpg://cinassist:cinassist@localhost:5432/cinassist` | Async-DB |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery-Broker + Pub/Sub |
| `WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | Transkription |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Lokales LLM |
| `OLLAMA_MODEL` | `llama3` | Beschreibung/Verfeinerung |
| `CLIP_MODEL` | `ViT-B/32` | Visuelle Embeddings |
| `CLAUDE_API_KEY` / `CLAUDE_MODEL` | `""` / `claude-3-5-sonnet-20241022` | optional |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | `""` / `gpt-4o` | optional |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `""` / `gemini-1.5-pro` | optional |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Systembinaries |

**Konstante Analyse-Parameter:** `AUDIO_SAMPLE_RATE = 16000`, `SCENE_THRESHOLD = 27.0`,
`CLIP_EMBEDDING_DIM = 512`. Verzeichnisse: `uploads/`, `outputs/`, `temp/`, `proxies/`
werden beim Start angelegt.

## 4.3 Phase 1 — Upload (`api/clips.py`)

**Soll-Verhalten von `POST /api/clips/upload`:**
1. Multipart-Datei + `quelle` ∈ {A, B} entgegennehmen.
2. Format + maximale Größe validieren (`NFR-12`).
3. Datei nach `uploads/{uuid}.<ext>` kopieren.
4. `Clip`-Datensatz (Status `hochgeladen`) + `Job` (Typ `ingestion`, Status `wartend`) anlegen.
5. Celery-Task `ingest` auslösen und `clip_id` + `job_id` zurückgeben.

## 4.4 Phase 2 — Ingestion (`workers/ingest.py`)

Asynchroner Celery-Task, Fortschritt 0 → 100 %, je Schritt Publish nach `job:{id}`.

| # | Schritt | Werkzeug | Ausgabe → DB |
|---|---------|----------|--------------|
| 1 | Metadaten | `ffprobe` | `Clip.dauer/aufloesung/bildrate/codec/dateigroesse` |
| 2 | Audio-Extraktion | FFmpeg → WAV 16 kHz Mono | `temp/{job}.wav` |
| 3 | Transkription | `mlx-whisper` large-v3-turbo | `Szene.transkription` + `transkription_json` (Wort-Timestamps) |
| 4 | Szenenerkennung | PySceneDetect `ContentDetector` (Schwelle 27) | `Szene`-Datensätze (start/end/dauer) |
| 5 | Visuelle Analyse | PIL, 3 Frames (25/50/75 %) | `Szene.analyse_visuelle` (Luminanz, Temperatur, Kontrast, Bewegung, Schärfe, Energie) |
| 6 | CLIP-Embedding | open-clip ViT-B/32 | `Szene.clip_embedding` (512-dim) |
| 7 | Beschreibung | LLaVA:7b (Vision) primär, LLaMA3 (Text) Fallback | `Szene.beschreibung` |
| 8 | Proxys | FFmpeg | Proxy-Video, Waveform-PNG, Thumbnail-Strip in `proxies/` |

Abschließend: `Clip.status = analysiert`, `Job.status = fertig`. Bei Fehler:
`Job.status = fehler` + Nachricht (`NFR-9`).

## 4.5 Phase 3 — KI-Schnitt (`api/ai.py`)

Vollständige Spezifikation in `07_KI_Schnitt_Spezifikation.md`. Überblick der 10 Stufen:
Szenen laden → Score/Energie (CLIP-Zero-Shot, Fallback Heuristik) → Qualitätsschwelle →
audio-bewusste Subdivision → Rollenklassifikation → kinematische Rollen → Bogen-Konstruktion
→ Beam Search (k=3) → Post-Processing → optionale LLM-Verfeinerung → Metriken + Timeline speichern.

Zusätzliche Endpunkte in `ai.py`: `/atlas` (PCA-Projektion), `/multicam` (Duplikate),
`/reorganize` (Neuanordnung bestehender Segmente), `/providers` (verfügbare LLMs).

## 4.6 Phase 4 — Export (`workers/export.py`)

Asynchroner Celery-Task. Soll-Ablauf:
1. Pro Segment schneiden: `ffmpeg -ss {mediaStart} -t {dauer} -i {datei}`.
2. Übergänge per `xfade`-Filter: `dissolve`, `fade`, `fadeblack`, `wipeleft`.
3. Audio-Endmischung (AAC).
4. Encoding H.264 → `outputs/{name}.mp4`.

## 4.7 Echtzeit (`api/websocket.py`)

- Endpunkt `WS /ws/jobs/{job_id}`.
- Abonniert den Redis-Kanal `job:{job_id}` (Pub/Sub).
- Leitet jede Nachricht als JSON `{status, progress, message, schritt?, schritt_daten?}` an
  den Client weiter (`AP-3`).

## 4.8 Persistenz (`core/database.py`)

- **Async-Engine** (`asyncpg`) für FastAPI-Endpunkte, **Sync-Engine** für Celery-Worker
  (`NFR-14`).
- `init_db()` legt Tabellen an und führt idempotente Spalten-Migrationen aus
  (`ADD COLUMN IF NOT EXISTS`).
- Datenmodell vollständig in `06_Datenmodell_Spezifikation.md`.

## 4.9 Abhängigkeiten (verbindlich, `requirements.txt`)

FastAPI 0.115, uvicorn 0.30, SQLAlchemy 2.0.35, asyncpg 0.29, Celery 5.4, Redis 5.0,
mlx-whisper 0.4, open-clip-torch 2.26.1, torch 2.4, pillow 10.4, scenedetect 0.6.4,
opencv-python-headless 4.10, numpy 1.26.4, httpx 0.27, anthropic ≥0.40, openai ≥1.50.

> **Soll-Hinweis:** Im Code referenzierte, aber in `requirements.txt` fehlende Pakete
> (z. B. `librosa` für Beat-Sync) MÜSSEN nachgetragen werden, damit das Setup reproduzierbar
> ist (`NFR-3`).
