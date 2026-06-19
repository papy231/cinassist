# 03 — Systemarchitektur

## 3.1 Schichtenmodell (Soll)

```
┌─────────────────────────────────────────────────────────────┐
│                     BENUTZER (Webbrowser)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / WebSocket
              ┌────────────▼────────────┐
              │  Next.js 16 (React 19)  │  localhost:3000
              │  TypeScript + Tailwind  │
              │  Zustand (State Mgmt)   │
              └────────────┬────────────┘
                           │ REST + WS
              ┌────────────▼────────────┐
              │  FastAPI (Python 3.12)  │  localhost:8001
              │  uvicorn (ASGI)         │  CORS, StaticFiles
              └────┬──────────┬─────────┘
                   │          │ async/await
        ┌──────────▼──┐   ┌───▼───────────────┐
        │ PostgreSQL  │   │  Celery Worker     │
        │ (SQLAlchemy │   │  (schwere Tasks)   │
        │  async)     │   │  Broker: Redis     │
        └─────────────┘   └───────┬───────────┘
                                  │
            ┌──────────┬──────────┼───────────┬──────────────┐
        ┌───▼───┐ ┌────▼────┐ ┌───▼────┐ ┌────▼─────┐ ┌──────▼──────┐
        │FFmpeg │ │mlx-     │ │open-   │ │PySceneD. │ │ Ollama /     │
        │       │ │whisper  │ │clip    │ │          │ │ LLaVA        │
        └───────┘ └─────────┘ └────────┘ └──────────┘ └─────────────┘
```

> **Hinweis zu Ports:** Der Frontend-API-Client (`src/lib/api.ts`) spricht das Backend
> unter **`localhost:8001`** an. Der Port MUSS konfigurierbar sein; Default = 8001.

## 3.2 Komponentenverantwortung

| Schicht | Aufgabe | Begründung (Soll) |
|---------|---------|-------------------|
| **Next.js** | Benutzeroberfläche, Timeline-Editor | App Router, clientseitiges Rendering für die Timeline |
| **FastAPI** | REST-API + WebSocket | nativ asynchron, Pydantic-Validierung |
| **Celery** | Lang laufende Tasks (Ingestion, Export) | Analyse dauert 1–5 Min → nicht-blockierend (`NFR-5a`) |
| **PostgreSQL** | Persistenz (Clips → Szenen → Jobs → Timelines) | relationale Daten, Embeddings als `ARRAY[Float]` |
| **Redis** | Celery-Broker + Pub/Sub | Queue + Echtzeit-Fortschritt über WebSocket |
| **Ollama** | Lokales LLM (LLaMA3 / LLaVA) | Beschreibungen + optionale Verfeinerung ohne Cloud |

## 3.3 Laufzeitprozesse (Soll-Deployment)

| Prozess | Port | Start |
|---------|------|-------|
| FastAPI / uvicorn | 8001 | `uvicorn backend.main:app` |
| Next.js | 3000 | `npm run dev` |
| Celery Worker | – | `celery -A backend.core.celery_app worker` |
| PostgreSQL | 5432 | Docker Compose |
| Redis | 6379 | Docker Compose |
| Ollama | 11434 | lokaler Dienst |

`docker-compose.yml` orchestriert PostgreSQL (persistentes Volume) und Redis (kein Volume).

## 3.4 Datenfluss (drei Phasen)

```
                ┌───────────── PHASE 1: UPLOAD ──────────────┐
Benutzer ──▶ POST /api/clips/upload ──▶ Clip+Job in DB ──▶ celery.delay(ingest)
                                                              │
                ┌───────────── PHASE 2: INGESTION ───────────┘
                ▼ Celery-Worker (workers/ingest.py)
          ffprobe → ffmpeg(WAV) → whisper → PySceneDetect
          → PIL-Analyse → CLIP-Embedding → LLaVA/LLaMA-Beschreibung
                │ je Schritt: Redis publish "job:{id}"
                ▼ WebSocket /ws/jobs/{id} → Fortschritt in UI

                ┌───────────── PHASE 3: KI-SCHNITT ──────────┐
Benutzer ──▶ POST /api/ai/cut ──▶ api/ai.py:
          Szenen laden → Energie/Score → Subdivision → Rollen
          → Bogen → Beam Search → Post-Processing → (opt.) LLM
                ▼ Timeline gespeichert + Metriken zurück

                ┌───────────── PHASE 4: EXPORT ──────────────┐
Benutzer ──▶ POST /api/export ──▶ Celery-Worker (workers/export.py):
          Segmente schneiden → xfade-Übergänge → Audiomix → H.264 MP4
```

Detail je Phase: `04_Backend_Spezifikation.md`. Detail des KI-Schnitts:
`07_KI_Schnitt_Spezifikation.md`. Vollständige Daten-Genealogie: `blueprint/DATEN_STAMMBAUM.md`.

## 3.5 Architekturprinzipien

- **AP-1 — Trennung schwerer Arbeit:** HTTP-Endpunkte starten nur Jobs; Rechenarbeit läuft
  in Celery-Workern (`NFR-5a`).
- **AP-2 — Async-first im Web-Layer:** FastAPI + SQLAlchemy async + asyncpg; Celery nutzt
  separate Sync-Engine (`NFR-14`).
- **AP-3 — Echtzeit über Pub/Sub:** Worker publizieren Fortschritt nach Redis; WebSocket
  abonniert und leitet an die UI weiter.
- **AP-4 — Lokal-zuerst:** Cloud ist optional und nur für Text (`NFR-1`, `NFR-2`).
- **AP-5 — Interpretierbarkeit vor Black-Box:** klassische, nachvollziehbare Algorithmen
  statt End-to-End-DL (`NFR-7`).
