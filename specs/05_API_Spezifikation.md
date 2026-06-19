# 05 — API-Spezifikation

> Basis-URL: `http://localhost:8001` · WebSocket: `ws://localhost:8001`
> Alle Bodies sind JSON, außer dem Upload (`multipart/form-data`).
> Quelle der Verträge: `backend/api/*.py` und `src/lib/api.ts`.

## 5.1 Überblick aller Endpunkte

| Methode | Pfad | Zweck | Anforderung |
|---------|------|-------|-------------|
| `GET` | `/` | Service-Info | – |
| `GET` | `/health` | Health-Check | NFR-17 |
| `POST` | `/api/clips/upload` | Clip hochladen + Ingestion starten | FR-1, FR-2 |
| `GET` | `/api/clips` | Alle Clips auflisten | FR-1 |
| `GET` | `/api/clips/{id}` | Clip-Details | FR-3 |
| `GET` | `/api/clips/{id}/analyse` | Szenen + Transkription + visuelle Analyse | FR-4–FR-7 |
| `GET` | `/api/clips/{id}/pipeline` | Pipeline-Bericht (Schritt-Historie + Szenen-Detail) | FR-13 |
| `DELETE` | `/api/clips/{id}` | Clip + Szenen + Jobs löschen | FR-1 |
| `POST` | `/api/ai/cut` | KI-Schnitt starten | FR-14–FR-25 |
| `POST` | `/api/ai/reorganize` | Bestehende Timeline neu ordnen | FR-29 |
| `POST` | `/api/ai/atlas` | 2D-PCA-Projektion der Embeddings | FR-31 |
| `POST` | `/api/ai/multicam` | Multicam-/Duplikat-Erkennung | FR-32 |
| `GET` | `/api/ai/providers` | Verfügbare LLM-Provider | FR-38 |
| `POST` | `/api/ai/chat` | Chat-Assistent über das Material | FR-33 |
| `POST` | `/api/timelines` | Timeline speichern | FR-26 |
| `GET` | `/api/timelines` | Timelines auflisten | FR-26 |
| `GET` | `/api/timelines/{id}` | Timeline laden | FR-26 |
| `PUT` | `/api/timelines/{id}` | Timeline aktualisieren | FR-26 |
| `DELETE` | `/api/timelines/{id}` | Timeline löschen | FR-26 |
| `POST` | `/api/export` | MP4-Export starten | FR-34, FR-35 |
| `POST` | `/api/export/open-in` | An externes NLE übergeben (FCPXML) | FR-36 |
| `WS` | `/ws/jobs/{job_id}` | Echtzeit Job-Fortschritt | FR-11 |

## 5.2 Clips

### `POST /api/clips/upload`
- **Request** (`multipart/form-data`): `datei` (File), `quelle` ∈ {`A`, `B`}.
- **Response 200**: `{ clip_id, job_id, dateiname, quelle, groesse_mb, nachricht }`.
- **Fehler**: 400 (ungültiges Format/Größe). Validierung gemäß `NFR-12`.

### `GET /api/clips/{id}/analyse`
- **Response 200** (`AnalyseDTO`):
  ```json
  {
    "clip": { "id", "dateiname", "quelle", "dauer", "aufloesung", "status" },
    "szenen_anzahl": 12,
    "szenen": [
      { "szenen_nr", "start_zeit", "end_zeit", "dauer",
        "thumbnail_pfad", "beschreibung", "transkription", "hat_embedding" }
    ]
  }
  ```

### `GET /api/clips/{id}/pipeline`
- **Response 200** (`PipelineBericht`): `{ clip_id, dateiname, schritt_history, szenen_detail[] }`,
  wobei `szenen_detail` u. a. `woerter_zeitstempel`, `analyse_visuelle`, `embedding_dimension`,
  `embedding_norm` enthält (Transparenz, `FR-13`).

## 5.3 KI-Schnitt

### `POST /api/ai/cut`
- **Request** (`AiCutRequest`):
  | Feld | Typ | Default | Bedeutung |
  |------|-----|---------|-----------|
  | `stil` | string | `"kinematisch"` | Schnittstil |
  | `prompt` | string? | `null` | Text-Prompt für inhaltliche Priorisierung (`FR-22`) |
  | `clip_ids` | string[] | — | zu verwendende Clips (Pflicht) |
  | `provider` | enum | `"ollama"` | `auto`/`ollama`/`claude`/`openai`/`gemini` |
  | `llm_modell` | string? | `null` | Modell überschreiben |
  | `llm_aktiviert` | bool | `false` | LLM-Verfeinerung an/aus (Default aus → `NFR-6`) |
  | `max_szenen` | int? | `null` | Obergrenze der Segmente |
  | `qualitaet_schwelle` | float | `0.0` | Mindest-Energie 0..1 |
  | `mit_uebergaengen` | bool | `false` | Crossfade/Wipe einfügen (Default harte Schnitte) |
  | `beat_sync` | bool | `false` | Schnitte auf Musik-Beats snappen (`FR-23`) |
  | `beat_pro_segment` | int | `4` | Beats pro Segment bei `beat_sync` |
- **Response 200** (`AiCutResult`):
  ```json
  {
    "timeline_id": "uuid",
    "segmente_anzahl": 14,
    "gesamtdauer": 63.2,
    "llm_provider": "ollama" | null,
    "scoring_methode": "clip_zero_shot" | "heuristik",
    "metriken": { "diversitaet", "wechselrate", "dialog_treue",
                  "szenen_anzahl", "uebergaenge", "prompt_relevance?" },
    "daten": { "segmente": [TimelineSegment], "gesamtdauer", "stil?" }
  }
  ```
- Algorithmus-Detail: `07_KI_Schnitt_Spezifikation.md`.

### `POST /api/ai/reorganize`
- **Request**: `{ segmente: ReorganizeSegment[] }` mit
  `{ id, clip_id?, szene_nr?, dauer, mediaStart, track, groupId?, label? }`.
- **Response 200**: `{ segmente[], anzahl, gesamtdauer, arc_rollen, methodik }`.
- Audio-Segmente folgen ihren Video-Geschwistern über `groupId`.

### `POST /api/ai/atlas`
- **Request**: `{ clip_ids?: string[], prompt?: string }`.
- **Response 200**: `{ scenes: [...] }` mit 2D-Koordinaten je Szene (PCA via numpy-SVD).

### `POST /api/ai/multicam`
- **Request**: `{ clip_ids: string[] }` (≥2). Audio-Chroma-Korrelation (librosa).
- **Response 200**: Beziehungsmatrix möglicher gleicher Szenen + Zeit-Offsets.

### `GET /api/ai/providers`
- **Response 200** (`ProvidersResult`): `{ verfuegbar: {provider→bool}, standard, modelle }`.
  Ohne API-Key bleibt Ollama verfügbar (`FR-39`).

## 5.4 Chat — `POST /api/ai/chat`
- **Request** (`ChatRequest`): `{ clip_ids: string[], messages: [{role, content}] }`.
- **Response 200** (`ChatResponse`): `{ message, proposed_prompt?, proposed_stil? }`.
  `proposed_stil` ∈ {energetisch, ausgewogen, ruhig}; kann direkt an `/api/ai/cut` übergeben werden.

## 5.5 Timelines
- `POST /api/timelines` — Body `{ name?, stil?, prompt?, daten }` → `TimelineDTO`.
- `PUT /api/timelines/{id}` — Body `{ name?, daten? }` → `TimelineDTO`.
- `GET /api/timelines`, `GET /api/timelines/{id}`, `DELETE /api/timelines/{id}`.

## 5.6 Export

### `POST /api/export`
- **Request** (`ExportRequest`): `{ segments: SegmentExport[], resolution="1920x1080", name="Export" }`
  mit `SegmentExport = { id, clip_id, track, start, dauer, mediaStart=0, transition? }`
  und `transition = { type="dissolve", dauer=0.5 }`.
- **Validierung**: ≥1 Video-Segment (Track beginnt mit `v`), sonst 400.
- **Response 200**: `{ job_id, nachricht }`. Fortschritt über WebSocket.

### `POST /api/export/open-in`
- **Request** (`SendToAppRequest`): `{ app: "davinci"|"premiere"|"fcp", fcpxml, name="CinAssist_Timeline" }`.
- Schreibt FCPXML und öffnet das Ziel-NLE (macOS `open -a`).

## 5.7 WebSocket — `WS /ws/jobs/{job_id}`
- Server sendet je Update ein JSON-Objekt:
  ```json
  { "status": "laeuft", "progress": 47, "message": "CLIP-Embedding…",
    "schritt": "embedding", "schritt_daten": { ... }, "result": null }
  ```
- `status` ∈ {`wartend`, `laeuft`, `fertig`, `fehler`}.

## 5.8 Allgemeine Konventionen
- **API-1** Fehler MÜSSEN als `{ "detail": "<Meldung>" }` mit passendem HTTP-Status (400/404/500) gemeldet werden.
- **API-2** Alle IDs sind UUID-Strings.
- **API-3** Zeitangaben in Sekunden (Float); Dateigrößen in MB im DTO, Bytes in der DB.
- **API-4** CORS MUSS den Frontend-Origin (`localhost:3000`) erlauben.
- **API-5** Statische Dateien (Thumbnails, Proxys, Outputs) werden über StaticFiles ausgeliefert.
