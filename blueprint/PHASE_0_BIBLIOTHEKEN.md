# Phase 0 — Inventar der eingesetzten Bibliotheken

> Übersicht **aller** Open-Source-Bibliotheken, die in CinAssist verwendet werden,
> mit Begründung, welche Elemente konkret eingesetzt werden, und warum die Wahl
> auf genau diese Bibliothek gefallen ist.

Dieses Dokument ist die **Grundlage** für die spezifischen Phasen (1 — Upload,
2 — Ingestion, 3 — KI-Schnitt, 4 — Export). Jede Phase referenziert Bibliotheken,
die hier mit Rolle und Lizenz erklärt sind.

---

## Inhaltsverzeichnis

- A — Backend-Infrastruktur (Web, Datenbank, Tasks)
- B — Backend-Medien & KI (Video, Audio, Vision, Sprache)
- C — Frontend (UI im Browser)
- D — Tabellarische Schnellübersicht

---

## A — Backend-Infrastruktur

### A.1 FastAPI (0.115) · MIT

**Was es ist.** Ein modernes Python-Framework für REST-APIs, async-nativ und auf
Typ-Annotationen basierend.

**Warum CinAssist es benutzt.** Es vereint drei Eigenschaften, die das Projekt
braucht: asynchrone Verarbeitung (damit lange Whisper- und CLIP-Aufrufe nicht den
Hauptthread blockieren), automatische Validierung der Eingaben über Pydantic, und
ein integriertes WebSocket-System für die Echtzeit-Fortschrittsanzeige.

**Konkret eingesetzte Elemente.**
- `APIRouter`, Decorators `@router.post(...)`, `@router.get(...)`
- `UploadFile`, `File(...)`, `Form(...)` für multipart-Uploads
- `Depends(get_db)` für die Datenbank-Session-Verwaltung
- `WebSocket` für die Job-Fortschrittsroute
- `HTTPException` für Fehlerantworten
- `CORSMiddleware`, `StaticFiles` (in `backend/main.py`)

**Ohne es** müsste die Routing-Logik, die Body-Parsing und die OpenAPI-Dokumentation
manuell implementiert werden.

---

### A.2 uvicorn (0.30) · BSD-3

**Was es ist.** Ein Hochleistungs-ASGI-Server für Python. ASGI = der async-Standard,
der WSGI ablöst.

**Warum CinAssist es benutzt.** FastAPI ist nur eine Anwendungs-Bibliothek; sie
braucht einen Server, der HTTP-Bytes vom Netzwerk entgegennimmt. uvicorn ist der
Standard im async-Python-Ökosystem.

**Konkret eingesetzte Elemente.**
- Der CLI-Befehl `uvicorn backend.main:app --port 8001 --reload`
- Der `--reload`-Modus für automatisches Neuladen während der Entwicklung
- Die integrierte WebSocket-Unterstützung (sonst müsste eine zusätzliche
  Bibliothek wie `wsproto` separat eingebunden werden)

**Ohne es** könnte FastAPI nicht auf einem Port lauschen.

---

### A.3 Pydantic (2.x) · MIT

**Was es ist.** Bibliothek zur Datenvalidierung über Python-Typannotationen.

**Warum CinAssist es benutzt.** Es ist in FastAPI eingebaut und garantiert,
dass alle Eingaben aus dem Browser bereits beim Routing-Schritt typisiert und
validiert werden — ohne manuellen Check-Code.

**Konkret eingesetzte Elemente.**
- `BaseModel`-Klassen für komplexe Request-Bodies (z. B. `AiCutRequest`
  in `backend/api/ai.py:124`)
- `Field(...)` mit Constraints (`ge=0.0`, `le=1.0`) zur Validierung
- Automatische Serialisierung der Response-Dictionaries in JSON

**Ohne es** müsste jeder Eingabe-Typ-Check (`isinstance(...)`, Bereichsprüfung)
manuell geschrieben werden.

---

### A.4 python-multipart (0.0.9) · Apache 2

**Was es ist.** Implementierung des HTTP-Encodings `multipart/form-data` für
Datei-Uploads.

**Warum CinAssist es benutzt.** Notwendig, damit der Upload-Endpoint
(`POST /api/clips/upload`) eine echte Datei aus dem HTTP-Body extrahieren kann.
Wird intern von FastAPI verwendet, sobald `UploadFile = File(...)` deklariert ist.

**Konkret eingesetzte Elemente.**
- Implizit über die FastAPI-Annotation `datei: UploadFile = File(...)`
- Streaming-Lesen des Datei-Inhalts via `await datei.read(1024 * 1024)`

**Ohne es** wären Datei-Uploads nicht möglich.

---

### A.5 SQLAlchemy (2.0) · MIT

**Was es ist.** Das De-facto-Standard-ORM (Object-Relational Mapper) für Python.

**Warum CinAssist es benutzt.** Die Daten in CinAssist sind stark relational
(Clip ↔ Szenen ↔ Jobs), das verlangt eine relationale Datenbank. SQLAlchemy
übersetzt Python-Klassen in SQL-Tabellen und schützt vor SQL-Injection.

**Konkret eingesetzte Elemente.**
- `DeclarativeBase` als Basis-Klasse für Modelle
- Modelle `Clip`, `Szene`, `Job`, `Timeline` (siehe
  `backend/core/database.py`)
- Spaltentypen: `String`, `Integer`, `Float`, `Text`, `DateTime`, `Boolean`,
  `JSON`, `ARRAY(Float)`, `UUID`
- Beziehungen: `relationship(...)` mit `cascade="all, delete-orphan"`
- Async-Session via `AsyncSession`, `async_sessionmaker`, `select(...)`,
  `await db.commit()`
- Sync-Session via `sessionmaker(bind=sync_engine)` für den Celery-Worker

**Ohne es** müsste SQL als String konstruiert werden — fehleranfällig und unsicher.

---

### A.6 asyncpg (0.29) · Apache 2 — und — psycopg2-binary (2.9) · LGPL

**Was es ist.** Zwei PostgreSQL-Treiber. **asyncpg** ist nativ asynchron und
sehr schnell; **psycopg2** ist der klassische synchrone Treiber.

**Warum CinAssist beide benutzt.** FastAPI braucht einen async-Treiber, damit
Datenbankabfragen die Event-Loop nicht blockieren. Der Celery-Worker dagegen
arbeitet **synchron**, weil dort eine eigene Prozess-Abstraktion vorliegt.
Beide werden parallel betrieben.

**Konkret eingesetzte Elemente.**
- asyncpg: implizit über `DATABASE_URL = "postgresql+asyncpg://..."`
- psycopg2-binary: implizit über `DATABASE_URL_SYNC = "postgresql://..."`
- Zwei getrennte Engines in `backend/core/database.py:17-22`

**Ohne sie** wäre keine Postgres-Kommunikation möglich.

---

### A.7 PostgreSQL (16) · PostgreSQL License

**Was es ist.** Ein ausgereiftes relationales Datenbanksystem mit Open-Source-Status
und ACID-Garantien.

**Warum CinAssist es benutzt.** Drei spezifische Anforderungen werden erfüllt,
die SQLite oder andere Datenbanken nicht bieten:
1. **Native JSON-Spalten** — für `szenen.analyse_visuelle` und
   `szenen.transkription_json`
2. **Native ARRAY-Spalten** — für `szenen.clip_embedding` (512-dim Float-Vektor)
3. **Robustes CASCADE-Delete** — wenn ein Clip gelöscht wird, sollen auch seine
   Szenen automatisch verschwinden

**Konkret eingesetzte Elemente.**
- 4 Tabellen: `clips`, `szenen`, `jobs`, `timelines`
- JSON-Typ in `szenen.analyse_visuelle`, `szenen.transkription_json`
- ARRAY-Typ in `szenen.clip_embedding`
- ForeignKey `clips.id → szenen.clip_id` mit `ondelete="CASCADE"`

**Ohne es** wären die Embeddings nur als String oder externe Datei speicherbar,
was Abfragen und Joins erschwert.

---

### A.8 Celery (5.4) · BSD-3

**Was es ist.** Das Standard-Framework für verteilte Hintergrundaufgaben in Python.

**Warum CinAssist es benutzt.** Eine vollständige Videoanalyse dauert mehrere
Minuten — viel zu lang für eine HTTP-Anfrage. Celery erlaubt es, die Aufgabe in
einen separaten Prozess auszulagern und dabei einen `task_id` zur Verfolgung zu
behalten.

**Konkret eingesetzte Elemente.**
- `Celery(...)`-App-Instanz in `backend/core/celery_app.py`
- Decorator `@celery_app.task(bind=True, name="cinassist.ingest")` an
  `ingestion_pipeline` (`backend/workers/ingest.py:617`) und
  `export_video_task` (`backend/workers/export.py`)
- `.delay(*args)` zum Senden einer Task ohne auf das Ergebnis zu warten
- `--pool=solo` als Ausführungsmodus (wichtig: kein fork, da PyTorch + Metal
  auf macOS fork nicht überstehen)

**Ohne es** müsste eine eigene Task-Queue implementiert oder die Analyse
synchron in der HTTP-Antwort durchgeführt werden — was zu Timeouts führen würde.

---

### A.9 Redis (7) + redis-py (5.0) · BSD-3 / MIT

**Was es ist.** Redis ist eine In-Memory-Datenbank. redis-py ist der Python-Client.

**Warum CinAssist es benutzt.** Doppelte Rolle:
1. **Broker für Celery** — die Warteschlange, in der FastAPI Tasks ablegt und
   die der Worker abarbeitet.
2. **Pub/Sub-Kanal** — der Worker publiziert Fortschritts-Nachrichten auf einem
   pro-Job-Kanal; die WebSocket-Route abonniert ihn und leitet die Nachrichten
   an den Browser weiter.

**Konkret eingesetzte Elemente.**
- Redis-Server auf `localhost:6379`
- redis-py-Client für `redis.from_url(...)`, `.publish(channel, json_payload)`
  (`backend/workers/ingest.py:59-66`)
- WebSocket-Subscriber in `backend/api/websocket.py`

**Ohne es** wäre Celery ohne Broker; und es gäbe keinen schnellen Pub/Sub-Kanal
für Live-Updates.

---

### A.10 websockets (12.0) · BSD-3

**Was es ist.** Eine Python-Bibliothek, die das WebSocket-Protokoll (RFC 6455)
implementiert.

**Warum CinAssist es benutzt.** HTTP ist Request/Response — der Server kann
nicht von sich aus eine Nachricht senden. Für Live-Fortschritt während der
mehrminütigen Analyse braucht es eine offene bidirektionale Verbindung.

**Konkret eingesetzte Elemente.**
- Implizit über FastAPI-`WebSocket` in `backend/api/websocket.py`
- Pings/Heartbeat über `WS_PING_INTERVAL = 20` aus `backend/core/config.py:52`

**Ohne es** müsste der Browser per Polling alle paar Sekunden den Job-Status
abfragen — höhere Latenz und mehr Netzwerklast.

---

## B — Backend-Medien & KI

### B.1 FFmpeg + ffprobe · LGPL / GPL

**Was es ist.** Das universelle Open-Source-Werkzeug zur Verarbeitung von Audio-
und Videodaten. ffprobe ist sein Inspektor-Pendant.

**Warum CinAssist es benutzt.** Es ist der Goldstandard für alles, was Pixel
und PCM-Samples angeht: Metadaten lesen, Audio extrahieren, Thumbnails
erstellen, Proxies kodieren, finale Videos exportieren. Keine Alternative
deckt diese Bandbreite ab.

**Konkret eingesetzte Elemente** (alle via `subprocess.run(...)`):
- `ffprobe` mit `-show_format -show_streams` — Metadaten als JSON
  (`backend/workers/ingest.py:73-79`)
- `ffmpeg -i ... -vn -acodec pcm_s16le -ar 16000 -ac 1` — Audio extrahieren
  als WAV 16 kHz Mono (`ingest.py:115-122`)
- `ffmpeg -ss ... -frames:v 1 -q:v 3 -vf scale=...` — Einzel-Frame-Extraktion
  für Thumbnails (`ingest.py:223-232`) und CLIP-Frames (`ingest.py:507-515`)
- `ffmpeg ... -c:v libx264 -preset fast -crf 26 -c:a aac` — Proxy-Erzeugung
  in 960p H.264 (`ingest.py:656-663`)
- `ffmpeg ... -filter_complex xfade=...` — Übergänge beim finalen Export
  (`backend/workers/export.py`)

**Ohne es** wären keine Video-Operationen möglich.

---

### B.2 mlx-whisper (0.4) · MIT

**Was es ist.** Eine Apple-MLX-optimierte Variante von OpenAI Whisper, der
State-of-the-Art-Spracherkennung.

**Warum CinAssist es benutzt.** Whisper liefert Transkriptionen mit
**Wort-Zeitstempeln**, was für audio-bewusstes Schnitt-Editing essentiell ist
(siehe Phase KI-Schnitt: Schnittpunkte werden an Sprachpausen ausgerichtet).
Die MLX-Variante nutzt die Neural Engine von Apple Silicon (M1/M2/M3) und
ist dort etwa 3× schneller als die Standard-PyTorch-Variante.

**Konkret eingesetzte Elemente.**
- `mlx_whisper.transcribe(audio_path, path_or_hf_repo=..., language="de",
  word_timestamps=True)` (`ingest.py:152-157`)
- Modell: `mlx-community/whisper-large-v3-turbo`
- Auswertung: `result["segments"]` mit `start`, `end`, `text`, `words` (mit
  pro-Wort-Zeitstempeln)

**Ohne es** wäre keine Transkription möglich; die A-Roll/B-Roll-Klassifikation
würde fehlen.

---

### B.3 PySceneDetect (0.6) · MIT

**Was es ist.** Eine Python-Bibliothek zur Erkennung von Szenenwechseln in
Videodateien.

**Warum CinAssist es benutzt.** Die gesamte Analyse arbeitet **pro Szene** —
Frames werden pro Szene extrahiert, CLIP-Embeddings werden pro Szene gerechnet,
LLaMA3 beschreibt pro Szene. Daher ist eine zuverlässige Szenenerkennung der
Grundstein.

**Konkret eingesetzte Elemente.**
- `open_video(video_pfad)` zum Öffnen
- `SceneManager()` als Orchestrator
- `ContentDetector(threshold=27.0)` als konkreter Algorithmus
  (HSV-Differenz-basiert)
- `detect_scenes(video)` zum Ausführen
- `get_scene_list()` zur Rückgabe der Schnitt-Liste

**Ohne es** müsste die Szenenerkennung manuell implementiert oder das ganze
Video als eine einzige Szene betrachtet werden.

---

### B.4 OpenCV — opencv-python-headless (4.10) · Apache 2

**Was es ist.** Die Standard-Bibliothek für Computer Vision in Python und C++.
Die `headless`-Variante kommt ohne GUI-Abhängigkeiten (kein GTK, kein Qt), was
in einem Server-Kontext ideal ist.

**Warum CinAssist es benutzt.** Wird von PySceneDetect intern verwendet, um
Video-Frames zu dekodieren. CinAssist selbst greift nicht direkt auf OpenCV
zurück, weil die einfacheren PIL-Operationen ausreichend sind.

**Konkret eingesetzte Elemente.** Nur indirekt über PySceneDetect.

**Ohne es** würde PySceneDetect nicht laufen.

---

### B.5 NumPy (1.26) · BSD-3

**Was es ist.** Die fundamentale Bibliothek für n-dimensionale Arrays und
mathematische Operationen in Python.

**Warum CinAssist es benutzt.** Wird intern von OpenCV, PySceneDetect und
PyTorch verwendet. Direkter Einsatz in CinAssist ist minimal — Listen-
Operationen reichen meistens aus.

**Konkret eingesetzte Elemente.** Hauptsächlich als Abhängigkeit von OpenCV,
PIL, PyTorch.

**Ohne es** würden die meisten anderen ML-Bibliotheken nicht funktionieren.

---

### B.6 PIL / Pillow (10.4) · HPND

**Was es ist.** Die Standard-Python-Bibliothek zur Bildverarbeitung. Pillow ist
der aktiv gepflegte Fork des originalen PIL.

**Warum CinAssist es benutzt.** Bewusst gewählt **statt OpenCV** für die
Visuelle Analyse, weil die Operationen (Luminanz, Kontrast, Bewegung, Schärfe)
sich auf einfache Pixel-Statistiken reduzieren lassen. PIL ist leichtgewichtiger
und benötigt keine zusätzlichen Native-Libraries.

**Konkret eingesetzte Elemente.**
- `Image.open(path).convert("RGB")` zum Laden eines extrahierten Frames
- `Image.resize((64, 64))` und `Image.resize((32, 32))` zum Downsampling
- `Image.getdata()` zum Auslesen der RGB-Tupel
- Eigene Implementierung der Laplace-Varianz für die Schärfemetrik
  (`ingest.py:332-359`)
- Eigene Implementierung der mittleren Pixel-Differenz für die
  Bewegungsmetrik (`ingest.py:321-329`)

**Ohne es** müsste OpenCV mit zusätzlicher Komplexität eingebunden werden.

---

### B.7 open-clip-torch (2.26) · MIT

**Was es ist.** Eine Open-Source-Reimplementierung von OpenAI's CLIP-Modell
(Contrastive Language-Image Pre-training) für PyTorch.

**Warum CinAssist es benutzt.** CLIP-Embeddings liefern eine 512-dim
semantische Repräsentation jedes Frames. Sie ermöglichen das Maß der
**visuellen Diversität** zwischen Szenen via Kosinus-Ähnlichkeit — eine
zentrale Eingabe des Beam-Search-Schnitt-Algorithmus.

**Konkret eingesetzte Elemente.**
- `open_clip.create_model_and_transforms("ViT-B/32", pretrained="openai")`
  (`ingest.py:497`)
- `preprocess(image)` zur Normalisierung des Eingabe-Frames
- `model.encode_image(image)` zur Erzeugung des 512-dim-Vektors
- L2-Normalisierung des Vektors für Kosinus-Vergleiche

**Ohne es** ginge der semantische Vergleich zwischen Szenen verloren.

---

### B.8 PyTorch (2.4) + torchvision (0.19) · BSD-3

**Was es ist.** Das von Meta entwickelte Deep-Learning-Framework. torchvision
liefert die Vision-spezifischen Erweiterungen.

**Warum CinAssist es benutzt.** Notwendig als Backend für open-clip-torch.
Außerdem wird die Apple-MPS-Beschleunigung (Metal Performance Shaders) genutzt,
sodass die CLIP-Inferenz auf der GPU eines Apple-Silicon-Macs läuft.

**Konkret eingesetzte Elemente.**
- `torch.backends.mps.is_available()` zur Geräteauswahl (`ingest.py:496`)
- `torch.no_grad()` als Kontext-Manager für Inferenz
- Tensor-Operationen `embedding / embedding.norm(dim=-1, keepdim=True)`

**Ohne es** würde CLIP nicht laufen.

---

### B.9 httpx (0.27) · BSD-3

**Was es ist.** Ein moderner Python-HTTP-Client mit Sync- und Async-API
(Nachfolger von `requests` mit async-Unterstützung).

**Warum CinAssist es benutzt.** Brauchen wir, um mit Ollama (lokales LLM) und
optional Claude/OpenAI/Gemini (Cloud-LLMs) zu sprechen. Die async-API passt
zum FastAPI-Stack.

**Konkret eingesetzte Elemente.**
- `httpx.post(...)` (synchron) im Celery-Worker für Ollama
  (`ingest.py:576-588`)
- `httpx.AsyncClient()` (async) in `backend/api/ai.py` für alle
  Cloud-Provider (Claude, OpenAI, Gemini)

**Ohne es** müsste ein anderer HTTP-Client wie `requests` für Sync und
`aiohttp` für Async kombiniert werden.

---

### B.10 anthropic SDK (≥0.40) · MIT

**Was es ist.** Das offizielle Python-SDK für die Claude-Familie von Anthropic.

**Warum CinAssist es benutzt.** Optional als hochwertiger Verfeinerungs-Provider
für den KI-Schnitt-Algorithmus. Wenn ein `CLAUDE_API_KEY` gesetzt ist, übergibt
das System die Beam-Search-Sequenz an Claude zur narrativen Optimierung.

**Konkret eingesetzte Elemente.** Tatsächlich wird Claude in CinAssist **direkt
per httpx** angesprochen statt über das SDK (`backend/api/ai.py:953-984`),
um die Abhängigkeit klein zu halten und die volle Kontrolle über die
HTTP-Schicht zu behalten. Das SDK wird nur als optionaler Import behalten.

**Ohne es** entfällt die Claude-Option; Ollama bleibt als Fallback.

---

### B.11 openai SDK (≥1.50) · Apache 2

**Was es ist.** Das offizielle Python-SDK für die OpenAI-API (GPT-4o,
o1-preview).

**Warum CinAssist es benutzt.** Gleiche Idee wie Anthropic: GPT-4o als
optionaler Verfeinerungs-Provider.

**Konkret eingesetzte Elemente.** Wie bei Anthropic: tatsächlicher Aufruf
erfolgt direkt per httpx (`backend/api/ai.py:987-1026`). Das SDK ist nur
optional installiert.

**Ohne es** entfällt die OpenAI-Option.

---

### B.12 Ollama + LLaMA3 · MIT (Ollama) / Llama Community License

**Was es ist.** Ollama ist ein lokales LLM-Runtime, das Modelle wie LLaMA3,
Mistral, Phi auf der eigenen Maschine ausführt. LLaMA3 ist das von Meta
veröffentlichte 8-Milliarden-Parameter-Modell.

**Warum CinAssist es benutzt.** Lokale Ausführung ohne API-Kosten und ohne
dass Inhalte das System verlassen. Wird für zwei Zwecke verwendet:
1. **Szenen-Beschreibungen** in der Ingestion-Pipeline (ein deutscher Satz
   pro Szene).
2. **Fallback-Provider** für den KI-Schnitt, wenn keine Cloud-API-Schlüssel
   konfiguriert sind.

**Konkret eingesetzte Elemente.**
- Ollama-HTTP-API auf `localhost:11434`
- Endpoint `POST /api/generate` mit JSON-Payload `{model: "llama3", prompt,
  stream: false, options: {temperature, num_predict}}`
- Modell `llama3:latest` (4.7 GB lokal)

**Ohne es** wäre keine vollständig lokale Pipeline möglich; man müsste auf
Cloud-LLMs angewiesen sein.

---

## C — Frontend

### C.1 Next.js (16.2.3) + React (19.2.4) · MIT

**Was es ist.** Next.js ist ein React-Framework mit serverseitigem Rendering,
App-Router und Turbopack als Build-System. React ist die zugrunde liegende
UI-Bibliothek.

**Warum CinAssist es benutzt.** Standard-Stack für moderne Web-UIs; passt zum
async-Stil des FastAPI-Backends und unterstützt die Komponenten-orientierte
Code-Organisation (jedes UI-Element ist eine Datei).

**Konkret eingesetzte Elemente.**
- App-Router (`src/app/editor/page.tsx`, `src/app/project/[id]/page.tsx`)
- Client-Components mit `"use client"`-Direktive
- Hooks: `useState`, `useEffect`, `useRef`, `useCallback`
- Server-seitige Redirects via `next.config.ts`

---

### C.2 TypeScript (5.x) · Apache 2

**Was es ist.** Statisch typisiertes JavaScript.

**Warum CinAssist es benutzt.** Frühe Fehlererkennung beim Aufruf des Backends,
klare Typen für die WebSocket-Nachrichten, IDE-Autovervollständigung.

**Konkret eingesetzte Elemente.**
- Typdefinitionen für die API-Schicht (`src/lib/api.ts`)
- Interfaces für `ClipDTO`, `JobUpdate`, `PipelineBericht`, `SzeneDetail`
- Generics in den Zustand-Stores

---

### C.3 Zustand (5.0) · MIT

**Was es ist.** Eine leichtgewichtige State-Management-Bibliothek für React
(eine schlanke Alternative zu Redux).

**Warum CinAssist es benutzt.** Die Editor-Oberfläche teilt globalen Zustand
zwischen vielen Komponenten (Liste der Clips, aktive Jobs, Timeline-Segmente,
Undo/Redo-Stack). Zustand macht das ohne Provider-Wrapper möglich.

**Konkret eingesetzte Elemente.**
- `useEditorStore` (`src/stores/editorStore.ts`)
- `useTimelineStore` (`src/stores/timelineStore.ts`)
- Selektoren, `set`/`get`-Pattern

---

### C.4 Tailwind CSS (4) · MIT

**Was es ist.** Utility-First-CSS-Framework — Klassen wie `flex`, `gap-4`,
`bg-blue-500` werden direkt im JSX verwendet.

**Warum CinAssist es benutzt.** Schnelle Iteration des Designs ohne separate
CSS-Dateien. Konsistenz durch Design-Tokens (`var(--bg1)`, `var(--text)`).

**Konkret eingesetzte Elemente.** Inline-Styles dominieren in CinAssist; Tailwind
wird hauptsächlich für globale CSS-Variablen genutzt (`src/app/globals.css`).

---

### C.5 Framer Motion (12) · MIT

**Was es ist.** Animations-Bibliothek für React.

**Warum CinAssist es benutzt.** Für sanfte Übergänge bei Panel-Öffnung,
Modal-Einblendung, Drag-and-Drop-Feedback.

**Konkret eingesetzte Elemente.** Animationsklassen und `motion`-Komponenten
in der Editor-UI.

---

### C.6 Lucide React (1.8) · ISC

**Was es ist.** Eine SVG-Icon-Bibliothek mit ~1.000 konsistenten Symbolen.

**Warum CinAssist es benutzt.** Konsistente Icons im UI ohne eigene SVG-Erstellung.

**Konkret eingesetzte Elemente.** Icons wie Upload, Trash, Plus,
Play/Pause werden importiert und in den Editor-Komponenten verwendet.

---

### C.7 clsx (2.1) · MIT

**Was es ist.** Kleines Utility zum bedingten Zusammenfügen von CSS-Klassen.

**Warum CinAssist es benutzt.** Vereinfacht Code wie
`className={clsx("base", isActive && "active", isDisabled && "disabled")}`.

**Konkret eingesetzte Elemente.** Punktuell in Komponenten zur Klassennamen-Kompositon.

---

## D — Tabellarische Schnellübersicht

| Phase / Schicht       | Bibliothek          | Hauptrolle                                      |
| --------------------- | ------------------- | ----------------------------------------------- |
| Phase 1 — Upload      | FastAPI             | HTTP-Routing                                    |
| Phase 1 — Upload      | uvicorn             | ASGI-Server                                     |
| Phase 1 — Upload      | Pydantic            | Eingabe-Validierung                             |
| Phase 1 — Upload      | python-multipart    | Datei-Body parsen                               |
| Phase 1 — Upload      | SQLAlchemy          | Clip + Job einfügen                             |
| Phase 1 — Upload      | asyncpg             | Async-DB-Treiber                                |
| Phase 1 — Upload      | PostgreSQL          | Persistierung                                   |
| Phase 1 — Upload      | Celery              | Task in Queue stellen                           |
| Phase 1 — Upload      | Redis + redis-py    | Broker                                          |
| Phase 1 — Upload      | websockets          | WS-Kanal für Fortschritt öffnen                 |
| Phase 2 — Ingestion   | psycopg2-binary     | Sync-DB-Treiber im Worker                       |
| Phase 2 — Ingestion   | FFmpeg + ffprobe    | Metadaten, Audio, Thumbnails, Proxy             |
| Phase 2 — Ingestion   | mlx-whisper         | Transkription mit Wort-Zeitstempeln             |
| Phase 2 — Ingestion   | PySceneDetect       | Szenenerkennung                                 |
| Phase 2 — Ingestion   | OpenCV (headless)   | Frame-Dekodierung (indirekt)                    |
| Phase 2 — Ingestion   | NumPy               | Array-Operationen (indirekt)                    |
| Phase 2 — Ingestion   | PIL / Pillow        | Pixel-Analyse (Luminanz, Kontrast, Mehr)        |
| Phase 2 — Ingestion   | open-clip-torch     | 512-dim CLIP-Embedding pro Szene                |
| Phase 2 — Ingestion   | PyTorch + torchvision | Backend für CLIP                              |
| Phase 2 — Ingestion   | httpx               | HTTP-Client zu Ollama                           |
| Phase 2 — Ingestion   | Ollama + LLaMA3     | Szenen-Beschreibungen                           |
| Phase 3 — KI-Schnitt  | httpx               | HTTP-Client zu Claude/OpenAI/Gemini             |
| Phase 3 — KI-Schnitt  | anthropic SDK       | (optional) Claude-Verfeinerung                  |
| Phase 3 — KI-Schnitt  | openai SDK          | (optional) GPT-4o-Verfeinerung                  |
| Phase 4 — Export      | FFmpeg              | xfade-Übergänge, Audio-Mix, H.264-Encoding      |
| Frontend              | Next.js + React     | UI-Framework                                    |
| Frontend              | TypeScript          | Typsicherheit                                   |
| Frontend              | Zustand             | State-Management                                |
| Frontend              | Tailwind CSS        | Utility-CSS                                     |
| Frontend              | Framer Motion       | Animationen                                     |
| Frontend              | Lucide React        | Icons                                           |
| Frontend              | clsx                | Conditional className                           |

---

## Anhang — Wie diese Wahl als bewusst dargestellt wird

Wenn der Prüfer fragt: *„Wieso so viele Bibliotheken?"*, lautet die Antwort:

> *„Jede Bibliothek erfüllt eine **klar abgegrenzte Verantwortung**. Es gibt keine
> Duplikate. Wenn ich eine Bibliothek austausche, bleibt der Rest des Systems
> intakt. Diese Modularität ist das Resultat der Schichtentrennung — Web-Schicht,
> Datenschicht, Aufgaben-Schicht, Medien-Schicht, KI-Schicht. **Jede Schicht
> hat ihre eigenen Standardwerkzeuge**, die ich aus dem etablierten Python- und
> JS-Ökosystem ausgewählt habe."*

Diese Aussage ist verteidigbar, weil sie der tatsächlichen Code-Struktur entspricht.

---

*Stand: 2026-05-21. Teil der Bachelorarbeit CinAssist — Verteidigungsdokumentation.*
