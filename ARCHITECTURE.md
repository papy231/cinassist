# CinAssist — Technische Dokumentation

> KI-gestützte Videoschnittpattform — 100 % lokal, Apple Silicon

---

## Inhaltsverzeichnis

1. [Überblick](#1-überblick)
2. [Gesamtarchitektur](#2-gesamtarchitektur)
3. [Backend — FastAPI](#3-backend--fastapi)
   - 3.1 Ingestion-Pipeline
   - 3.2 KI-Schnitt-Algorithmus
   - 3.3 REST-API
   - 3.4 Videoexport
4. [Frontend — Next.js](#4-frontend--nextjs)
5. [Datenbank](#5-datenbank)
6. [Infrastruktur](#6-infrastruktur)
7. [Bibliotheken und Abhängigkeiten](#7-bibliotheken-und-abhängigkeiten)
8. [Vollständiger Datenfluss](#8-vollständiger-datenfluss)
9. [Schlüsselalgorithmen](#9-schlüsselalgorithmen)
10. [Technische Entscheidungen](#10-technische-entscheidungen)

---

## 1. Überblick

CinAssist ist ein intelligenter nichtlinearer Videoeditor (NLE), der den kinematografischen Schnitt mithilfe einer mehrstufigen KI-Analysekette automatisiert. Der Nutzer importiert Rohvideos; das System erzeugt vollautomatisch eine montierte Timeline mit Übergängen und narrativem Bogen.

**Typischer Anwendungsfall:**
```
2 Rohdateien .mp4 (Clip A + Clip B)
        ↓  Import
Automatische Analyse: ca. 2–5 Minuten
        ↓  KI-Schnitt
Montierte Timeline: ca. 30–120 Sekunden mit narrativem Bogen
        ↓  Export
Fertiges MP4 mit Übergängen
```

**Grundprinzip:** Alles läuft lokal. Keine Videodaten verlassen den Rechner. Cloud-LLMs (Claude, GPT-4, Gemini) sind optional und verarbeiten ausschließlich Beschreibungstext — niemals Pixel.

---

## 2. Gesamtarchitektur

```
┌─────────────────────────────────────────────────────────────┐
│                        BENUTZER                             │
│                    (Webbrowser)                             │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / WebSocket
          ┌────────────▼────────────┐
          │   Next.js 16  (React)   │  localhost:3000
          │   TypeScript + Tailwind │
          │   Zustand (State Mgmt)  │
          └────────────┬────────────┘
                       │ REST API + WS
          ┌────────────▼────────────┐
          │  FastAPI (Python 3.12)  │  localhost:8000
          │  uvicorn (ASGI)         │
          │  CORS, StaticFiles      │
          └────┬──────────┬─────────┘
               │          │ async/await
    ┌──────────▼──┐   ┌───▼───────────────┐
    │ PostgreSQL  │   │  Celery Workers    │
    │ (SQLAlchemy │   │  (schwere Tasks)   │
    │  async)     │   │  Broker: Redis     │
    └─────────────┘   └───────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
        ┌─────▼─────┐   ┌──────▼──────┐  ┌─────▼──────┐
        │  FFmpeg   │   │ mlx-whisper │  │ open-clip  │
        │ (Video,   │   │ (Whisper    │  │ (CLIP      │
        │  Audio,   │   │  large-v3)  │  │  ViT-B/32) │
        │  Export)  │   │ Apple MLX   │  │  PyTorch)  │
        └───────────┘   └─────────────┘  └────────────┘
              │
        ┌─────▼──────────────┐
        │  PySceneDetect     │
        │  (Szenenerkennung  │
        │  ContentDetector)  │
        └────────────────────┘
```

### Warum diese Schichtentrennung?

| Schicht | Aufgabe | Begründung |
|---------|---------|------------|
| Next.js | Benutzeroberfläche | React Server Components, App Router, clientseitiges Rendering für die Timeline |
| FastAPI | REST-API + WebSocket | Nativ asynchron, Pydantic-Validierung, performant |
| Celery | Lang laufende Tasks | Videoanalyse = 1–5 Min. → nicht blockierend, Jobs verfolgbar |
| PostgreSQL | Datenpersistenz | Relationale Daten (Clips → Szenen → Timelines) |
| Redis | Broker + Pub/Sub | Celery-Queue + Echtzeit-Fortschrittsübertragung via WebSocket |

---

## 3. Backend — FastAPI

### Dateistruktur

```
backend/
├── main.py              # FastAPI-Einstiegspunkt, Middlewares, Router
├── core/
│   ├── config.py        # Umgebungsvariablen, Pfade, API-Schlüssel
│   ├── database.py      # SQLAlchemy-Modelle + async/sync Engines
│   └── celery_app.py    # Celery-Konfiguration
├── api/
│   ├── clips.py         # Upload, Liste, Analyse, Löschung
│   ├── ai.py            # KI-Schnitt-Algorithmus (Kern des Systems)
│   ├── timelines.py     # CRUD Timelines
│   ├── export.py        # FFmpeg-Export-Job starten
│   └── websocket.py     # Echtzeit Job-Fortschritt
└── workers/
    ├── ingest.py        # Ingestion-Pipeline (Celery Task)
    └── export.py        # Video-Export (Celery Task)
```

---

### 3.1 Ingestion-Pipeline

Wird automatisch bei jedem Upload gestartet. Asynchroner Celery-Task mit Fortschritt 0 → 100 %.

```
Hochgeladene Videodatei
        │
        ▼ SCHRITT 1 — ffprobe (Metadaten)
        │  Dauer, Auflösung, Codec, FPS, Dateigröße
        │
        ▼ SCHRITT 2 — FFmpeg (Audio-Extraktion)
        │  → WAV 16 kHz Mono → /temp/{job_id}.wav
        │
        ▼ SCHRITT 3 — mlx-whisper (Transkription)
        │  Modell: whisper-large-v3-turbo (Apple MLX)
        │  → Text + Timestamps je Wort/Segment
        │  → als JSON gespeichert in Szene.transkription_json
        │
        ▼ SCHRITT 4 — PySceneDetect (Szenenerkennung)
        │  Algorithmus: ContentDetector, Schwelle = 27.0
        │  → Liste von (start_frame, end_frame) pro Szene
        │  → Thumbnail-Extraktion je Szene (FFmpeg)
        │
        ▼ SCHRITT 5 — Visuelle Multi-Frame-Analyse (PIL, v4)
        │  Pro Szene: Extraktion von 3 Frames (25% / 50% / 75%)
        │  Berechnung:
        │    Luminosität   → normierter RGB-Mittelwert
        │    Temperatur    → R/B-Verhältnis (warm / neutral / kalt)
        │    Kontrast      → Standardabweichung der Luminanz
        │    Bewegung      → Inter-Frame-Pixeldifferenz (F25→F50, F50→F75)
        │    Schärfe       → Laplace-Varianz (Sharpness Score)
        │    Qualität      → Schärfe × Belichtungsfaktor
        │    Energie       → Kontrast×0.40 + Bewegung×0.35
        │                     + Luminosität×0.15 + Schärfe×0.10
        │
        ▼ SCHRITT 6 — CLIP-Embedding (open_clip, ViT-B/32)
        │  → 512-dimensionaler Vektor pro Szene
        │  → visuelle Ähnlichkeitsmessung zwischen Szenen
        │
        ▼ SCHRITT 7 — Beschreibung LLaMA3 (Ollama, lokal)
           → 1 beschreibender Satz pro Szene
           → gespeichert in Szene.beschreibung
```

**Echtzeit-Verfolgung:** Jeder Schritt veröffentlicht seinen Fortschritt via Redis Pub/Sub → WebSocket → Fortschrittsbalken in der UI.

---

### 3.2 KI-Schnitt-Algorithmus (`api/ai.py`)

Dies ist das intellektuelle Herzstück des Systems. Der Endpunkt `POST /api/ai/cut` orchestriert 5 Analysestufen.

#### Stufe 1 — Energie-Berechnung

Jede Szene erhält einen Energiewert (0–1) aus den Ingestion-Daten:

```
Energie = Kontrast × 0.40
        + Bewegung × 0.35    ← echter Temporal-Flow (3 Frames)
        + Luminosität × 0.15
        + Schärfe × 0.10     ← Qualitätsfilter (unscharfe Szenen abgewertet)
```

#### Stufe 2 — A-Roll / B-Roll Klassifikation

Jede Szene wird nach ihrer narrativen Rolle kategoriisiert:

| Typ | Kriterien | Rolle im Schnitt |
|-----|-----------|-----------------|
| **A-Roll** | Transkription vorhanden + geringe Bewegung | Interview, Sprecheraufnahme (primär) |
| **B-Roll** | Kein Dialog + hohe Bewegung/Kontrast | Schnittbild, Aktion, Detail (sekundär) |
| **Establishing** | Kein Dialog + hell + ruhig + lang | Eröffnungseinstellung (führt Ort ein) |

#### Stufe 3 — Kinematischer Bogen

Für den Stil `kinematisch` werden Szenen narrativen Rollen zugewiesen:

```
OUVERTURE  → visuell stark, Clip-Anfang, kein Dialog
ACTION     → dynamisches B-Roll, hoher Kontrast/Bewegung
TRANSITION → A-Roll, Dialog, narratives Bindeglied
CLIMAX     → maximale Energie + hohe Bewegung
CLOTURE    → ruhig, warm/neutral, Clip-Ende
```

Der Bogen wird in 6 proportionalen Phasen aufgebaut:

```
[1 Ouverture] → [25% Action] → [20% Transition] → [25% Action] → [1-2 Climax] → [1 Cloture]
```

**Nachbearbeitungsregeln:**
- Clip-Wechsel-Korrektur: niemals denselben Quell-Clip zweimal hintereinander (4 Passes)
- Long/Short-Regulierung: niemals 3 Szenen > 6s aufeinanderfolgend
- A/B-Roll-Alternierung: zwei aufeinanderfolgende A-Rolls werden durch ein B-Roll getrennt

#### Stufe 4 — Beam Search

Die verbleibenden Szenen werden per **Beam Search (Breite 3)** geordnet statt greedy:

```python
# Globale Bewertungsfunktion einer Sequenz
Score = mittlere_Energie   × 0.25
      + visuelle_Diversität × 0.35   ← PIL-Histogramm-Abstand
      + A/B-Alternierung    × 0.25
      + Clip-Wechsel        × 0.15
```

Der Beam Search hält 3 Kandidatensequenzen parallel und gibt die global beste zurück — vermeidet lokale Optima des Greedy-Ansatzes.

#### Stufe 5 — LLM-Verfeinerung (optional)

Die finale Sequenz wird an ein LLM übergeben, das die Reihenfolge für narrative Kohärenz optimieren kann:

```
Unterstützte Provider (automatische Priorität):
  1. Claude 3.5 Sonnet  (Anthropic) — CLAUDE_API_KEY
  2. GPT-4o             (OpenAI)    — OPENAI_API_KEY
  3. Gemini 1.5 Pro     (Google)    — GEMINI_API_KEY
  4. LLaMA3             (Ollama)    — lokal, immer verfügbar
```

Der System-Prompt ist je nach gewähltem Stil angepasst (kinematisch, dokumentarisch, Werbespot, Kurzfilm, Social Media). Die JSON-Antwort wird robust extrahiert (Unterstützung von Reasoning-Text und JSON-Modus der LLMs).

#### Audio-aware Unterteilung

Lange Szenen werden vor dem Algorithmus unterteilt. Schnittpunkte werden an **Whisper-Pausen ausgerichtet** (Stille > 300 ms), um Schnitte mitten in Sätzen zu vermeiden.

---

### 3.3 REST-API

| Methode | Endpunkt | Beschreibung |
|---------|----------|--------------|
| `POST` | `/api/clips/upload` | Video hochladen (A oder B), Ingestion starten |
| `GET` | `/api/clips` | Alle Clips auflisten |
| `GET` | `/api/clips/{id}` | Clip-Details |
| `GET` | `/api/clips/{id}/analyse` | Szenen + Transkription + visuelle Analyse |
| `DELETE` | `/api/clips/{id}` | Clip + Szenen + Jobs löschen |
| `POST` | `/api/ai/cut` | KI-Schnitt starten |
| `GET` | `/api/ai/providers` | Verfügbare LLM-Provider + Modelle |
| `GET` | `/api/timelines` | Gespeicherte Timelines auflisten |
| `POST` | `/api/timelines` | Timeline speichern |
| `DELETE` | `/api/timelines/{id}` | Timeline löschen |
| `POST` | `/api/export` | MP4-Export starten (FFmpeg, async Job) |
| `WS` | `/ws/jobs/{job_id}` | Echtzeit Job-Fortschritt |

---

### 3.4 Videoexport

Der Export wandelt die Timeline-JSON via FFmpeg in eine MP4-Datei um:

1. Pro Segment: `ffmpeg -ss {mediaStart} -t {dauer} -i {datei}` → temporäre Datei
2. Zusammenfügen mit Übergängen via FFmpeg `xfade`-Filter:
   - `dissolve` — Überblende
   - `fade` / `fadeblack` — Einblende aus Weiß/Schwarz
   - `wipeleft` — Wischblende links
3. Audio-Endmischung
4. Encoding H.264 / AAC → MP4

---

## 4. Frontend — Next.js

### Dateistruktur

```
src/
├── app/
│   ├── layout.tsx         # Root-Layout — Schriftart Helvetica Neue, Providers
│   ├── globals.css        # CSS-Variablen: Farben, Typografie
│   ├── page.tsx           # Startseite / Dashboard
│   └── editor/
│       └── page.tsx       # Haupt-Editor
├── components/
│   ├── AppSidebar.tsx     # Seitennavigation
│   ├── Dock.tsx           # Werkzeugleiste unten
│   ├── ProjectCard.tsx    # Projektkarte im Dashboard
│   ├── Timeline/
│   │   └── TimelineEditor.tsx   # Mehrspurige Timeline (Drag & Drop)
│   └── Viewer/
│       └── DualViewer.tsx       # A/B Vorschau nebeneinander
├── lib/
│   └── api.ts             # HTTP-Client zu FastAPI (fetch-Wrapper)
├── stores/
│   ├── editorStore.ts     # Globaler Editor-State (Zustand)
│   └── timelineStore.ts   # Timeline-State: Clips, Playhead, Zoom
└── hooks/
    └── useJobStatus.ts    # WebSocket-Hook für Job-Verfolgung
```

### Zustandsverwaltung — Zustand

Zwei getrennte Stores (Prinzip der Einzelverantwortung):

**`editorStore`** — Globaler Editor-Zustand:
- Liste der hochgeladenen Clips (`ClipDTO[]`)
- Aktive Jobs mit Fortschritt
- Backend-Verbindungsstatus
- Undo/Redo Stack (Timeline-Verlauf)

**`timelineStore`** — Timeline-Zustand:
- Auf der Timeline platzierte Clips (`TimelineClip[]`)
- Playhead-Position (in Pixeln)
- Zoom (Pixel pro Sekunde)
- Ausgewählte Spur
- KI-Banner (verwendeter Stil, Provider, Segmentanzahl)

### Timeline-Editor

Die Timeline ist eine mehrspurige canvas-ähnliche Oberfläche in React/CSS:
- **Zoom**: 1 px = N Sekunden, dynamisch anpassbar
- **Drag & Drop**: Clips auf der Timeline verschieben
- **Split**: Clip an der Klickposition teilen
- **Übergänge**: visuell zwischen Clips dargestellt
- **Farben**: Orange = Clip A, Blau = Clip B, Grün = Audio

### KI-Provider-Auswahl

Das KI-Panel im Editor zeigt die 5 verfügbaren Provider. Ein grüner Punkt zeigt an, dass ein API-Schlüssel für den Provider konfiguriert ist. Der Provider „Auto" wählt automatisch den besten verfügbaren aus.

---

## 5. Datenbank

### PostgreSQL-Schema

```
clips
├── id (UUID, PK)
├── dateiname, dateipfad, quelle (A/B)
├── dauer, aufloesung, bildrate, codec
└── status: hochgeladen → analysiert → fehler

    │ 1:N
    ▼
szenen
├── id (UUID, PK)
├── clip_id (FK → clips)
├── szenen_nr, start_zeit, end_zeit, dauer
├── thumbnail_pfad
├── clip_embedding (ARRAY[Float], 512-dim CLIP-Vektor)
├── beschreibung   (TEXT, LLaMA3-Beschreibung)
├── transkription  (TEXT, Whisper-Text)
├── transkription_json (JSON, [{start, end, text}])
└── analyse_visuelle (JSON, {luminosite, temperature,
                              kontrast, mouvement,
                              schaerfe, qualitaet, energie})

jobs
├── id (UUID, PK)
├── clip_id (FK → clips)
├── typ: ingestion / export
├── status: wartend → laeuft → fertig → fehler
├── fortschritt (0–100)
├── nachricht, ergebnis (JSON)
└── celery_task_id

timelines
├── id (UUID, PK)
├── name, stil, prompt
├── daten (JSON -- vollständige Segmentsequenz)
└── gesamtdauer
```

---

## 6. Infrastruktur

### Benötigte Dienste

| Dienst | Port | Aufgabe |
|--------|------|---------|
| FastAPI/uvicorn | 8000 | Haupt-API |
| Next.js | 3000 | Benutzeroberfläche |
| PostgreSQL | 5432 | Datenbank |
| Redis | 6379 | Celery-Broker + WebSocket-Pub/Sub |
| Ollama | 11434 | Lokales LLM (LLaMA3) |

### Docker Compose

`docker-compose.yml` orchestriert PostgreSQL und Redis:

```yaml
services:
  db:      postgres:16  # persistentes Volume cinassist_pgdata
  redis:   redis:7      # keine Persistenz (nur Broker)
```

### Umgebungsvariablen

```bash
DATABASE_URL=postgresql+asyncpg://cinassist:cinassist@localhost:5432/cinassist
REDIS_URL=redis://localhost:6379/0
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
OLLAMA_MODEL=llama3
CLIP_MODEL=ViT-B/32

# Optional (Cloud-LLM)
CLAUDE_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

---

## 7. Bibliotheken und Abhängigkeiten

### Backend Python

| Bibliothek | Version | Verwendung |
|---|---|---|
| **FastAPI** | 0.115 | REST-API + WebSocket-Framework, Pydantic-Validierung |
| **uvicorn** | 0.30 | ASGI-Server |
| **SQLAlchemy** | 2.0 | Async-ORM für PostgreSQL |
| **asyncpg** | 0.29 | Nativer async PostgreSQL-Treiber |
| **Celery** | 5.4 | Verteilte Task-Queue (Ingestion- und Export-Worker) |
| **Redis** | 5.0 | Celery-Broker + Pub/Sub-Kanal für Echtzeit-WebSocket |
| **mlx-whisper** | 0.4 | Speech-to-Text, Modell large-v3-turbo via Apple MLX |
| **open-clip-torch** | 2.26 | Visuelle CLIP-Embeddings ViT-B/32 (512-dim, Kosinus-Ähnlichkeit) |
| **PyTorch** | 2.4 | Tensor-Backend für open-clip |
| **Pillow (PIL)** | 10.4 | Pixel-Analyse (Luminosität, Kontrast, Laplace-Schärfe) |
| **PySceneDetect** | 0.6 | Automatische Szenenerkennung via ContentDetector |
| **OpenCV** | 4.10 | Von PySceneDetect für die Videoverarbeitung genutzt |
| **FFmpeg** | System | Audio-Extraktion, Thumbnails, xfade-Übergänge, Export |
| **httpx** | 0.27 | Async HTTP-Client (Ollama + Cloud-LLM APIs) |
| **anthropic** | 0.40 | SDK Claude 3.5 Sonnet/Opus |
| **openai** | 1.50 | SDK GPT-4o / o1 |
| **numpy** | 1.26 | Matrizenoperationen (PySceneDetect, OpenCV) |

### Frontend JavaScript/TypeScript

| Bibliothek | Version | Verwendung |
|---|---|---|
| **Next.js** | 16.2.3 | React-Framework, App Router, Server/Client Components |
| **React** | 19.2.4 | Deklarative UI, Hooks, Komponenten |
| **TypeScript** | 5 | Statische Typisierung |
| **Zustand** | 5.0 | Leichtgewichtige Zustandsverwaltung (Redux-Alternative) |
| **Tailwind CSS** | 4 | Utility-First CSS, Design-System |
| **Framer Motion** | 12.38 | Flüssige Animationen (Panels, UI-Übergänge) |
| **Lucide React** | 1.8 | SVG-Icon-Bibliothek |
| **clsx** | 2.1 | Bedingte CSS-Klassen-Komposition |

---

## 8. Vollständiger Datenfluss

```
+----------+
| Benutzer | -- lädt .mp4-Datei hoch
+----+-----+
     |  POST /api/clips/upload (multipart/form-data)
     v
+---------------------+
| clips.py            | -- validiert Format + Größe (max 5 GB)
| POST /upload        | -- kopiert nach /backend/uploads/{uuid}.mp4
+--------+------------+ -- erstellt Clip + Job in DB
         |  celery.delay(ingestion_pipeline)
         v
+----------------------+
| workers/ingest.py    | -- Celery-Task (separater Thread)
| ingestion_pipeline   |
|                      | 1. ffprobe  --> Metadaten
|                      | 2. ffmpeg   --> WAV 16 kHz
|                      | 3. whisper  --> Transkriptions-JSON
|                      | 4. PySceneD --> Szenen-Liste
|                      | 5. PIL      --> 3-Frame-Analyse / Szene
|                      | 6. CLIP     --> 512-dim Embedding
|                      | 7. Ollama   --> 1-Satz-Beschreibung
|                      |
| pub je Schritt  -->  | --> Redis-Kanal "job:{id}"
+----------------------+
         |  WebSocket-Push
         v
+---------------------+
| websocket.py        | -- Redis Pub/Sub-Abonnent
| /ws/jobs/{job_id}   | -- JSON-Push an Frontend
+---------------------+
         |
         v  Frontend empfängt {status, progress, message}
            --> Fortschrittsbalken in der UI

-------- KI-SCHNITT ------------------------------------------

     | POST /api/ai/cut {stil, clip_ids, provider}
     v
+----------------------------------------------------+
| api/ai.py                                          |
|                                                    |
|  1. Lädt alle Szenen aus PostgreSQL                |
|  2. Berechnet Energie (PIL + CLIP + Heuristik)     |
|  3. Wendet Qualitätsschwelle an                    |
|  4. Unterteilt lange Szenen (audio-aware)          |
|  5. Erkennt A-Roll / B-Roll / Establishing         |
|  6. Weist kinematische Rollen zu (5 Typen)         |
|  7. Baut narrativen Bogen (6 Phasen)               |
|  8. Füllung via Beam Search (Breite = 3)           |
|  9. Korrekturen: Clip-Wechsel, Long/Short, A/B     |
| 10. LLM-Verfeinerung (Claude/GPT-4/Gemini/Ollama)  |
|                                                    |
|  --> gibt geordnete Segmentliste zurück            |
+----------------------------------------------------+
         |
         v  Frontend: Timeline mit farbigen Segmenten + Übergängen

-------- EXPORT ----------------------------------------------

     | POST /api/export {segments, resolution}
     v
+---------------------+
| workers/export.py   | -- Celery-Task
|                     | 1. Schneidet Segmente (ffmpeg -ss -t)
|                     | 2. xfade-Übergänge einfügen
|                     | 3. Audio-Endmischung AAC
|                     | 4. H.264 --> /backend/outputs/{name}.mp4
+---------------------+
```

---

## 9. Schlüsselalgorithmen

### 9.1 Szenenerkennung (PySceneDetect)

Verwendet den `ContentDetector` auf Basis der HSV-Differenz zwischen aufeinanderfolgenden Frames.
Schwelle = 27,0 (empirisch). Darunter: Szene läuft weiter. Darüber: Schnitt erkannt.

### 9.2 Schärfe-Score (Laplace-Approximation, PIL)

Laplace-Approximation auf einem 32x32-Graustufenbild, ohne OpenCV:

```
L(x,y) = -4 * I(x,y) + I(x-1,y) + I(x+1,y) + I(x,y-1) + I(x,y+1)

Var(L) --> 0 : unscharf  |  Var(L) --> hoch : scharf

Schärfe = min(1.0, Var(L) / 600.0)
```

### 9.3 Visuelles CLIP-Embedding

Jede Szene wird durch einen 512-dimensionalen Vektor (ViT-B/32) repräsentiert.
Die Ähnlichkeit zweier Szenen wird über die Kosinus-Ähnlichkeit berechnet:

```
        a . b
sim = ---------
      ||a|| ||b||
```

Visuelle Diversität = 1 - sim(a, b), wird bei der Selektion maximiert.

### 9.4 Beam Search (Breite = 3)

Suchalgorithmus zum Ordnen der verbleibenden Szenen:

```
Schleife solange Szenen nicht platziert:
  Für jeden aktiven Beam (max 3):
    Bewerte alle möglichen Erweiterungen (lokaler Score)
    Behalte Top-3 Erweiterungen
  Sortiere alle Beam-Kandidaten (globaler Score)
  Behalte Top-3 Beams

Globaler Score einer Sequenz:
  mittl. Energie   x 0.25
  + vis. Diversität x 0.35
  + A/B-Alternierung x 0.25
  + Clip-Wechsel   x 0.15
```

---

## 10. Technische Entscheidungen

| Entscheidung | Begründung |
|---|---|
| **Apple MLX für Whisper** | 3x schneller als Standard-CPU auf Apple Silicon (M1/M2/M3). Kein NVIDIA-GPU erforderlich. |
| **Reines PIL für Bildanalyse** | Vermeidet OpenCV als schwere Abhängigkeit für einfache Pixeloperationen. |
| **Celery + Redis** | Videoanalysen (2-5 Min.) müssen außerhalb des HTTP-Threads laufen. Celery ermöglicht Tracking und Retry. |
| **Zustand statt Redux** | Minimale API, kein Boilerplate, direkte Mutations in den Stores. Passend für diese Projektgröße. |
| **SQLAlchemy async** | FastAPI ist async-first. SQLAlchemy 2.0 mit asyncpg liefert nicht-blockierende Datenbankabfragen. |
| **CLIP ViT-B/32** | Gutes Größe/Qualität-Verhältnis (151 MB). 512-dim-Embeddings ermöglichen semantische Ähnlichkeitsmessung ohne spezifisches Training. |
| **Multi-Provider LLM** | Nutzer mit Claude-Schlüssel verwenden Claude; ohne Schlüssel läuft automatisch Ollama lokal. |
| **A/B-Roll-Erkennung ohne ML** | Einfache Heuristiken (Transkription vorhanden + Bewegungsniveau) vermeiden ein trainiertes Klassifikationsmodell. |

---

*CinAssist v0.1 -- Backend: Python 3.12 + FastAPI | Frontend: Next.js 16 + React 19*

