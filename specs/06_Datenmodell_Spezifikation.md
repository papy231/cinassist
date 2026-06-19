# 06 — Datenmodell-Spezifikation

> Persistenz: PostgreSQL über SQLAlchemy 2.0. Quelle: `backend/core/database.py`.
> DTOs (Übertragungsobjekte zum Frontend): `src/lib/api.ts`.

## 6.1 Entity-Relationship-Übersicht

```
clips (1) ──< (N) szenen
clips (1) ──< (N) jobs
timelines  (eigenständig; referenziert Clips/Szenen nur als JSON)
```

## 6.2 Tabelle `clips`  (DM-1)

| Spalte | Typ | Constraints | Bedeutung |
|--------|-----|-------------|-----------|
| `id` | UUID | PK | |
| `dateiname` | String(255) | NOT NULL | Originaldateiname |
| `dateipfad` | String(512) | NOT NULL | Pfad in `uploads/` |
| `quelle` | String(1) | NOT NULL | `"A"` oder `"B"` |
| `dauer` | Float | nullable | Sekunden |
| `aufloesung` | String(20) | nullable | z. B. `"1920x1080"` |
| `bildrate` | Float | nullable | FPS |
| `codec` | String(50) | nullable | |
| `dateigroesse` | Integer | nullable | Bytes |
| `erstellt_am` | DateTime | default now | |
| `status` | String(20) | default `"hochgeladen"` | `hochgeladen` → `analysiert` → `fehler` |

**Lebenszyklus (DM-1a):** `hochgeladen` (nach Upload) → `analysiert` (Ingestion fertig) →
`fehler` (bei Abbruch). Kaskade: Löschen eines Clips löscht zugehörige `szenen` und `jobs`.

## 6.3 Tabelle `szenen`  (DM-2)

| Spalte | Typ | Bedeutung |
|--------|-----|-----------|
| `id` | UUID PK | |
| `clip_id` | UUID FK → clips (ON DELETE CASCADE) | |
| `szenen_nr` | Integer | laufende Nummer im Clip |
| `start_zeit` / `end_zeit` / `dauer` | Float | Sekunden |
| `thumbnail_frame` | Integer? | Frame-Nummer |
| `thumbnail_pfad` | String(512)? | |
| `clip_embedding` | ARRAY(Float)? | 512-dim CLIP-Vektor (`DM-2a`) |
| `beschreibung` | Text? | LLaVA/LLaMA-Beschreibung |
| `transkription` | Text? | Whisper-Text des Segments |
| `transkription_json` | JSON? | Whisper mit Wort-Zeitstempeln |
| `analyse_visuelle` | JSON? | siehe `DM-2b` |

**DM-2a — Embedding:** `clip_embedding` MUSS Länge `CLIP_EMBEDDING_DIM = 512` haben, sofern
gesetzt. Ähnlichkeit zweier Szenen = Kosinus-Ähnlichkeit; Diversität = 1 − Kosinus.

**DM-2b — `analyse_visuelle` (JSON-Schema, Soll):**
```json
{
  "luminosite": 0.0,   // 0..1 mittlere Helligkeit
  "temperature": 0.0,  // R/B-Verhältnis (warm/neutral/kalt)
  "contraste": 0.0,    // 0..1 Standardabweichung der Luminanz
  "mouvement": 0.0,    // 0..1 Inter-Frame-Pixeldifferenz (3 Frames)
  "schaerfe": 0.0,     // 0..1 Laplace-Varianz (32×32)
  "qualitaet": 0.0,    // 0..1 Schärfe × Belichtung
  "energie": 0.0       // 0..1 gewichtete Kombination (siehe AI-1)
}
```

## 6.4 Tabelle `jobs`  (DM-3)

| Spalte | Typ | Bedeutung |
|--------|-----|-----------|
| `id` | UUID PK | |
| `typ` | String(50) | `ingestion` / `extend` / `export` |
| `clip_id` | UUID FK? | optional (Export hat keinen Clip) |
| `celery_task_id` | String(255)? | |
| `status` | String(20) | `wartend` → `laeuft` → `fertig` / `fehler` |
| `fortschritt` | Integer | 0–100 |
| `nachricht` | Text? | menschenlesbarer Status |
| `ergebnis` | JSON? | Endergebnis (z. B. Ausgabepfad) |
| `erstellt_am` / `aktualisiert_am` | DateTime | |

**DM-3a:** Der WebSocket-Vertrag (`05_API §5.7`) spiegelt `status`, `fortschritt`,
`nachricht`. Jeder Statuswechsel MUSS `aktualisiert_am` setzen.

## 6.5 Tabelle `timelines`  (DM-4)

| Spalte | Typ | Bedeutung |
|--------|-----|-----------|
| `id` | UUID PK | |
| `name` | String(255) default `"Unbenannt"` | |
| `stil` | String(50)? | verwendeter Schnittstil |
| `prompt` | Text? | ggf. verwendeter Text-Prompt |
| `daten` | JSON NOT NULL | komplette Segmentsequenz (`DM-4a`) |
| `gesamtdauer` | Float? | Sekunden |
| `erstellt_am` | DateTime | |

**DM-4a — `daten` (Timeline-JSON, Soll):**
```json
{
  "segmente": [
    {
      "id": "uuid",
      "clip_id": "uuid",
      "szene_nr": 3,
      "label": "ACTION",
      "track": "v1",
      "start": 12.5,       // Position auf der Timeline (s)
      "dauer": 3.2,        // Länge (s)
      "quelle": "A",       // "A" | "B" | "audio" | "music"
      "ai": true           // vom KI-Schnitt erzeugt?
    }
  ],
  "gesamtdauer": 63.2,
  "stil": "kinematisch"
}
```

## 6.6 DTO-Mapping (DB → Frontend)

| DB | DTO (`api.ts`) | Abweichung |
|----|----------------|-----------|
| `Clip` | `ClipDTO` | `dateigroesse` (Bytes) → `dateigroesse_mb` (MB); zusätzlich `video_url`, `proxy_url`, `waveform_url`, `strip_url` (aus `proxies/`) |
| `Szene` | `SzeneDTO` / `SzeneDetail` | `clip_embedding` → `hat_embedding` (bool) bzw. `embedding_norm`/`embedding_dimension` |
| `Job` | `JobUpdate` | nur Laufzeit-Felder `status`/`progress`/`message`/`schritt` |
| `Timeline` | `TimelineDTO` | 1:1; `daten` als `TimelineDaten` typisiert |

**DM-5:** DTOs DÜRFEN keine internen Pfade ungefiltert exponieren, außer `dateipfad`, das
bewusst für den NLE-Export ans Frontend gegeben wird.

## 6.7 Migrationen
`init_db()` MUSS idempotent sein: Tabellen via `create_all`, fehlende Spalten via
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (aktuell: `szenen.analyse_visuelle`).
