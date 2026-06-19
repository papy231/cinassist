# Daten-Stammbaum — Phase 1 bis 4

> Dieser Stammbaum verfolgt **jedes einzelne Datenobjekt** von dem Moment,
> in dem der Nutzer ein Video hochlädt, bis zur fertigen exportierten
> Videodatei. Für jedes Objekt ist festgehalten: **wer es erzeugt**
> (Werkzeug/Funktion), **woraus es entsteht** (Eltern) und **wo es liegt**
> (Datenbank, Festplatte oder Arbeitsspeicher).
>
> Zweck: in der mündlichen Verteidigung lückenlos belegen können, *was im
> Hintergrund passiert* — kein Datenobjekt ohne nachvollziehbare Herkunft.

Quellcode-Stand direkt aus dem Repository gelesen:
`backend/api/clips.py`, `backend/workers/ingest.py`, `backend/api/ai.py`,
`backend/workers/export.py`, `backend/core/database.py`.

---

## 1. Wie man diesen Stammbaum liest

Jeder Knoten hat eine **ID** in der Form `[Phase.Nummer]` (z. B. `[2.9]`).
Die Zeile **`Eltern:`** verweist auf die ID(s), aus denen das Objekt
abgeleitet wurde. So lässt sich jede Abstammung rückwärts bis zum
Roh-Video `[0]` verfolgen.

**Vier Generationen** — eine pro Verarbeitungsphase:

| Generation | Phase | Ort im Code | Ausführung |
| ---------- | ----- | ----------- | ---------- |
| **0** | Roh-Video | Browser → Upload | — |
| **1** | Synchroner Upload | `api/clips.py` | FastAPI-Hauptthread, < 1 s |
| **2** | Asynchrone Ingestion | `workers/ingest.py` | Celery-Worker, Minuten |
| **3** | KI-Schnitt | `api/ai.py` | FastAPI-Request, Sekunden |
| **4** | Export | `workers/export.py` | Celery-Worker, Minuten |

**Legende der Symbole:**

```
Erzeuger : die Funktion oder das Werkzeug, das das Objekt herstellt
Inhalt   : die konkreten Felder / der Wert des Objekts
Ablage   : wo das Objekt gespeichert wird (DB-Tabelle / Datei / RAM)
Eltern   : die ID(s) der Vorgänger-Objekte (Abstammung)
◄══      : Blatt des Baums — Endprodukt einer Linie
```

---

## 2. Der Stammbaum

```
┌──────────────────────────────────────────────────────────────────────┐
│ [0]  ROH-VIDEO                                          GENERATION 0  │
│      Der Nutzer wählt im Browser eine Videodatei und eine Quelle.     │
│      Inhalt : datei = MP4/MOV/AVI/MKV/WEBM-Bytes  ·  quelle = "A"|"B" │
│      Ablage : HTTP-Request-Body (multipart/form-data)                 │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
╔═══════════════════════════════╧══════════════════════════════════════╗
║ PHASE 1 — SYNCHRONER UPLOAD                              GENERATION 1 ║
║ backend/api/clips.py · clip_hochladen()                               ║
╚═══════════════════════════════╤══════════════════════════════════════╝
                                │
   ├─ [1.1] VIDEO-DATEI AUF FESTPLATTE
   │         Erzeuger : open(ziel,"wb") — Streaming in 1-MB-Chunks
   │         Inhalt   : das unveränderte Original-Video
   │         Ablage   : backend/uploads/{clip_id}.{ext}
   │         Eltern   : [0]
   │
   ├─ [1.2] DB-ZEILE  »clips«
   │         Erzeuger : SQLAlchemy INSERT (db.add + commit)
   │         Inhalt   : id, dateiname, dateipfad→[1.1], quelle,
   │                    dateigroesse, status="hochgeladen"
   │                    (dauer, aufloesung, bildrate, codec = NULL)
   │         Ablage   : PostgreSQL · Tabelle clips
   │         Eltern   : [0], [1.1]
   │
   ├─ [1.3] DB-ZEILE  »jobs«
   │         Erzeuger : SQLAlchemy INSERT
   │         Inhalt   : id, typ="ingestion", clip_id→[1.2],
   │                    status="wartend", fortschritt=0, celery_task_id
   │         Ablage   : PostgreSQL · Tabelle jobs
   │         Eltern   : [1.2]
   │
   └─ [1.4] CELERY-TASK-NACHRICHT
             Erzeuger : ingestion_pipeline.delay(clip_id, job_id)
             Inhalt   : Task "cinassist.ingest" + Argumente (clip_id, job_id)
             Ablage   : Redis-Warteschlange "celery"
             Eltern   : [1.2], [1.3]
                                │
╔═══════════════════════════════╧══════════════════════════════════════╗
║ PHASE 2 — ASYNCHRONE INGESTION                           GENERATION 2 ║
║ backend/workers/ingest.py · ingestion_pipeline()  (Celery-Worker)     ║
║ Auslöser: der Worker entnimmt [1.4] aus der Redis-Warteschlange.      ║
╚═══════════════════════════════╤══════════════════════════════════════╝
                                │
   ├─ [2.1] METADATEN  →  UPDATE »clips«
   │         Erzeuger : ffprobe (-show_format -show_streams)
   │         Inhalt   : dauer, aufloesung, bildrate, codec
   │         Ablage   : PostgreSQL · clips (Felder von [1.2] gefüllt)
   │         Eltern   : [1.1]
   │
   ├─ [2.2] PROXY-VIDEO
   │         Erzeuger : FFmpeg (scale max 960px, libx264, CRF 26, AAC)
   │         Inhalt   : leichte Vorschau-Version fürs Browser-Playback
   │         Ablage   : backend/proxies/{stem}_proxy.mp4
   │         Eltern   : [1.1]
   │
   ├─ [2.3] WAVEFORM-BILD
   │         Erzeuger : FFmpeg (showwavespic, 1920×80)
   │         Inhalt   : PNG der Audio-Wellenform für die Timeline-Anzeige
   │         Ablage   : backend/proxies/{stem}_wf.png
   │         Eltern   : [1.1]
   │
   ├─ [2.4] THUMBNAIL-STRIP
   │         Erzeuger : FFmpeg (fps + tile 24×1)
   │         Inhalt   : 24 Vorschaubilder in einer Reihe (Timeline-Streifen)
   │         Ablage   : backend/proxies/{stem}_strip.jpg
   │         Eltern   : [1.1]
   │
   ├─ [2.5] AUDIO-SPUR  (temporär)
   │    │     Erzeuger : FFmpeg (-vn, pcm_s16le, 16 kHz, mono)
   │    │     Inhalt   : WAV PCM 16-bit — exakt das von Whisper erwartete Format
   │    │     Ablage   : backend/temp/{uuid}_audio.wav
   │    │     Eltern   : [1.1]      Hinweis: wird nach [2.6] gelöscht
   │    │
   │    └─ [2.6] TRANSKRIPTION  (im Arbeitsspeicher)
   │              Erzeuger : mlx-whisper (Modell whisper-large-v3-turbo)
   │              Inhalt   : text, sprache, segmente[] — jedes Segment mit
   │                         Wort-Zeitstempeln (start/end pro Wort)
   │              Filter   : Stille-Vorprüfung (RMS) + Halluzinations-Filter
   │              Ablage   : Python-dict (noch nicht in der DB)
   │              Eltern   : [2.5]
   │
   ├─ [2.7] SZENEN-LISTE  +  SZENEN-THUMBNAILS
   │    │     Erzeuger : PySceneDetect ContentDetector (HSV, Schwelle 27.0)
   │    │                + FFmpeg (1 Thumbnail je Szene, 320 px breit)
   │    │     Inhalt   : je Szene: szenen_nr, start_zeit, end_zeit, dauer
   │    │     Ablage   : Liste im RAM · Thumbnails in temp/thumbs_{clip_id}/
   │    │     Eltern   : [1.1]
   │    │
   │    │   ╶╴ aus jeder Szene [2.7] entstehen drei Kind-Daten ╶╴
   │    │
   │    ├─ [2.8] VISUELLE ANALYSE  (je Szene)
   │    │         Erzeuger : PIL — 3 Frames pro Szene (25 % / 50 % / 75 %)
   │    │         Inhalt   : luminosite, temperature, kontrast, mouvement,
   │    │                    schaerfe, qualitaet, energie
   │    │                    energie = kontrast·0.40 + mouvement·0.35
   │    │                            + luminosite·0.15 + schaerfe·0.10
   │    │         Ablage   : Python-dict (→ später Feld analyse_visuelle)
   │    │         Eltern   : [2.7], [1.1]
   │    │
   │    ├─ [2.9] CLIP-EMBEDDING  (je Szene)
   │    │         Erzeuger : open_clip ViT-B/32 (Mittelpunkt-Frame der Szene)
   │    │         Inhalt   : 512-dimensionaler, L2-normalisierter Vektor
   │    │         Ablage   : Liste[float] (→ später Feld clip_embedding)
   │    │         Eltern   : [2.7]
   │    │
   │    └─ [2.10] BESCHREIBUNG  (je Szene)
   │              Erzeuger : LLaVA:7b (Vision-Modell auf dem Thumbnail)
   │                         Fallback: LLaMA3 (Text aus Dialog) via Ollama
   │              Inhalt   : 2-3 sachliche Sätze zum Bildinhalt
   │              Ablage   : String (→ später Feld beschreibung)
   │              Eltern   : [2.7]-Thumbnail, [2.6]-Dialog (nur Fallback)
   │
   └─ [2.11] DB-ZEILEN  »szenen«  (N Zeilen)  +  UPDATE clips.status
              Erzeuger : SQLAlchemy INSERT (eine Zeile je Szene)
              Inhalt je Zeile :
                  clip_id, szenen_nr, start_zeit, end_zeit, dauer,
                  thumbnail_pfad,
                  clip_embedding      ◄─ [2.9]
                  beschreibung        ◄─ [2.10]
                  transkription       ◄─ [2.6]  (passendes Zeit-Segment)
                  transkription_json  ◄─ [2.6]  (Segmente + Wort-Stempel)
                  analyse_visuelle    ◄─ [2.8]
              Zusätzlich: clips.status = "analysiert"
              Ablage   : PostgreSQL · Tabelle szenen
              Eltern   : [2.6], [2.7], [2.8], [2.9], [2.10]
                                │
╔═══════════════════════════════╧══════════════════════════════════════╗
║ PHASE 3 — KI-SCHNITT                                     GENERATION 3 ║
║ backend/api/ai.py · POST /api/ai/cut · ai_schnitt()                   ║
║ Auslöser: der Nutzer startet einen Schnitt (Stil + optional Prompt).  ║
╚═══════════════════════════════╤══════════════════════════════════════╝
                                │
   ├─ [3.1] SZENEN-OBJEKTE IM SPEICHER
   │         Erzeuger : SELECT szenen WHERE clip_id IN (clip_ids)
   │         Inhalt   : alle Felder aus [2.11] als Python-dicts
   │         Ablage   : Liste im RAM
   │         Eltern   : [2.11]
   │
   ├─ [3.2] _energie  (Szenen-Score, je Szene)
   │         Erzeuger : _szene_energie() — CLIP-Zero-Shot:
   │                    cos(Embedding, "action") − cos(Embedding, "calm")
   │         Inhalt   : Score 0–1   (Fallback: heuristische energie aus [2.8])
   │         Eltern   : [2.9]  (Fallback-Eltern: [2.8])
   │
   ├─ [3.3] _prompt_relevance  (je Szene · nur wenn ein Prompt eingegeben wurde)
   │         Erzeuger : CLIP-Text-Encoder(prompt) → Kosinus zu jeder Szene
   │         Inhalt   : Relevanz-Score 0–1 zum Nutzer-Prompt
   │         Eltern   : [2.9], Nutzer-Prompt
   │
   ├─ [3.4] SUB-SZENEN  (lange Szenen werden geteilt)
   │         Erzeuger : _subdivise_scenes() — Schnittpunkte bevorzugt an
   │                    Whisper-Sprechpausen (> 300 ms) statt mitten im Wort
   │         Inhalt   : kürzere Szenen mit Sinus-Energieprofil
   │         Eltern   : [3.1], [2.6]
   │
   ├─ [3.5] _typ_narratif  (je Szene)
   │         Erzeuger : _detecte_role_narratif()
   │         Inhalt   : "a_roll" | "b_roll" | "establishing"
   │         Eltern   : [2.8] (Bewegung/Helligkeit), [2.6] (Dialog vorhanden?)
   │
   ├─ [3.6] _rolle  (je Szene)
   │         Erzeuger : _rolle_kinematisch()
   │         Inhalt   : "ouverture" | "action" | "transition" | "climax"
   │                    | "cloture"  — Rolle im dramatischen Bogen
   │         Eltern   : [3.2], [3.5]
   │
   ├─ [3.7] GEORDNETE SEQUENZ
   │         Erzeuger : einer von drei Pfaden —
   │                    • Bogen-Pfad : _baue_kinematischen_bogen()
   │                      inkl. BEAM SEARCH (Breite 3) für die globale Ordnung
   │                    • Prompt-Pfad: MMR-Re-Ranking (Relevanz vs. Diversität)
   │                    • Einfach-Pfad: _baue_einfachen_schnitt()
   │                    optional danach: LLM-Verfeinerung
   │                    (Claude / GPT-4o / Gemini / Ollama)
   │         Inhalt   : die Szenen in finaler Reihenfolge
   │         Eltern   : [3.4], [3.5], [3.6]  + visuelle Diversität aus [2.9]
   │
   ├─ [3.8] TIMELINE-SEGMENTE  (Spur V1 + Spur A1)
   │         Erzeuger : _baue_timeline()
   │         Inhalt   : je Segment: start, dauer, mediaStart, clip_id,
   │                    track, rolle, energie, ggf. transition
   │         Eltern   : [3.7]
   │
   ├─ [3.9] EVALUATIONS-METRIKEN
   │         Erzeuger : _berechne_metriken()
   │         Inhalt   : diversitaet, wechselrate, dialog_treue
   │         Eltern   : [3.7], [2.9], [2.6]
   │
   └─ [3.10] DB-ZEILE  »timelines«
              Erzeuger : SQLAlchemy INSERT
              Inhalt   : daten (JSON: segmente + arc_rollen + metriken),
                         gesamtdauer, stil, prompt
              Ablage   : PostgreSQL · Tabelle timelines
              Eltern   : [3.8], [3.9]
                                │
╔═══════════════════════════════╧══════════════════════════════════════╗
║ PHASE 4 — EXPORT                                         GENERATION 4 ║
║ backend/workers/export.py · export_video_task()  (Celery-Worker)      ║
║ Auslöser: der Nutzer startet den Export der Timeline.                 ║
╚═══════════════════════════════╤══════════════════════════════════════╝
                                │
   ├─ [4.1] DATEIPFAD-AUFLÖSUNG  clip_id → dateipfad
   │         Erzeuger : _resolve_clips() — SELECT clips WHERE id IN (...)
   │         Inhalt   : Zuordnung jedes Segments zu seiner Original-Datei
   │         Eltern   : [3.8], [1.2]
   │
   ├─ [4.2] FFMPEG-BEFEHL
   │         Erzeuger : _build_ffmpeg_cmd()
   │         Inhalt   : ein Input je Segment (-ss/-t) + filter_complex:
   │                    xfade-Kette (Video) + acrossfade-Kette (Audio)
   │         Eltern   : [3.8], [4.1]
   │
   └─ [4.3] FINALE VIDEO-DATEI                                      ◄══
             Erzeuger : FFmpeg (libx264 CRF 18, AAC 192k, +faststart)
             Inhalt   : der fertig geschnittene Film
             Ablage   : backend/outputs/export_{job_id}.mp4
             Eltern   : [4.2]  +  die Original-Videos [1.1]
```

---

## 3. Der Kreis schließt sich — woher die finalen Pixel stammen

Ein zentraler Punkt für die Verteidigung: Die **Bild- und Tonpixel** der
finalen Datei `[4.3]` stammen **nicht** aus Proxy `[2.2]`, Embedding `[2.9]`
oder Beschreibung `[2.10]`. FFmpeg liest in Phase 4 direkt die
**Original-Dateien `[1.1]`** ein und schneidet sie gemäß der Timeline.

Die gesamte Analyse-Generation 2 (Transkription, Szenen, Embeddings,
Beschreibungen) und die Generation 3 (Energie, Rollen, Sequenz) bilden also
einen **Entscheidungs-Stammbaum**: Sie bestimmen *welche Ausschnitte in
welcher Reihenfolge* verwendet werden — die eigentliche Substanz kommt aber
zur Wurzel `[1.1]` zurück. Der Baum verzweigt sich zur Analyse und führt für
den Export wieder zum Ursprung.

```
   [1.1] Original-Video ──┬──► Analyse-Äste (Gen. 2 + 3) ──► Schnitt-Plan
                          │                                       │
                          └──────── Pixel-Quelle ────────────────►┤
                                                                   ▼
                                                          [4.3] Finale Datei
```

---

## 4. Stammbaum pro Generation — Kurzübersicht

| ID | Objekt | Erzeuger / Werkzeug | Ablage |
| -- | ------ | ------------------- | ------ |
| `[0]` | Roh-Video | Nutzer / Browser | HTTP-Body |
| `[1.1]` | Video-Datei | open() Streaming | uploads/ |
| `[1.2]` | Zeile clips | SQLAlchemy INSERT | PostgreSQL |
| `[1.3]` | Zeile jobs | SQLAlchemy INSERT | PostgreSQL |
| `[1.4]` | Celery-Nachricht | .delay() | Redis |
| `[2.1]` | Metadaten | ffprobe | PostgreSQL (clips) |
| `[2.2]` | Proxy-Video | FFmpeg | proxies/ |
| `[2.3]` | Waveform-Bild | FFmpeg | proxies/ |
| `[2.4]` | Thumbnail-Strip | FFmpeg | proxies/ |
| `[2.5]` | Audio-Spur | FFmpeg | temp/ (temporär) |
| `[2.6]` | Transkription | mlx-whisper | RAM → szenen |
| `[2.7]` | Szenen + Thumbnails | PySceneDetect + FFmpeg | RAM + temp/ |
| `[2.8]` | Visuelle Analyse | PIL | RAM → szenen |
| `[2.9]` | CLIP-Embedding | open_clip ViT-B/32 | RAM → szenen |
| `[2.10]` | Beschreibung | LLaVA:7b / LLaMA3 | RAM → szenen |
| `[2.11]` | Zeilen szenen | SQLAlchemy INSERT | PostgreSQL |
| `[3.1]` | Szenen-Objekte | SQLAlchemy SELECT | RAM |
| `[3.2]` | _energie | CLIP Zero-Shot | RAM |
| `[3.3]` | _prompt_relevance | CLIP-Text-Encoder | RAM |
| `[3.4]` | Sub-Szenen | _subdivise_scenes | RAM |
| `[3.5]` | _typ_narratif | _detecte_role_narratif | RAM |
| `[3.6]` | _rolle | _rolle_kinematisch | RAM |
| `[3.7]` | Geordnete Sequenz | Beam Search / MMR / LLM | RAM |
| `[3.8]` | Timeline-Segmente | _baue_timeline | RAM |
| `[3.9]` | Metriken | _berechne_metriken | RAM |
| `[3.10]` | Zeile timelines | SQLAlchemy INSERT | PostgreSQL |
| `[4.1]` | Pfad-Auflösung | _resolve_clips | RAM |
| `[4.2]` | FFmpeg-Befehl | _build_ffmpeg_cmd | RAM |
| `[4.3]` | Finale Video-Datei | FFmpeg | outputs/ |

---

## 5. Kernfragen für die Verteidigung

### Frage: „Welche Datenobjekte werden dauerhaft gespeichert?"

> Vier in PostgreSQL: eine Zeile `clips`, eine Zeile `jobs`, N Zeilen
> `szenen` und eine Zeile `timelines`. Drei Dateitypen auf der Festplatte:
> das Original in `uploads/`, die Vorschau-Dateien in `proxies/` und die
> finale Datei in `outputs/`. Temporäre Objekte (Audio-Spur `[2.5]`,
> Szenen-Thumbnails) liegen in `temp/`; die Audio-Spur wird nach der
> Transkription gelöscht.

### Frage: „Wo entstehen die CLIP-Embeddings und wozu dienen sie?"

> Erzeugt werden sie in Phase 2, Knoten `[2.9]`, mit open_clip ViT-B/32 aus
> dem Mittelpunkt-Frame jeder Szene — ein 512-dim-Vektor je Szene, gespeichert
> im Feld `clip_embedding`. Verwendet werden sie in Phase 3 gleich dreifach:
> für den Energie-Score `[3.2]` (Zero-Shot „action vs. calm"), für die
> Prompt-Relevanz `[3.3]` und für die visuelle Diversität in der
> Beam-Search-Bewertung `[3.7]` sowie in der Metrik `[3.9]`.

### Frage: „Was ist der Unterschied zwischen `analyse_visuelle.energie` und `_energie`?"

> `analyse_visuelle.energie` `[2.8]` ist die heuristische Pixel-Formel aus
> Phase 2 (gewichtete Summe aus Kontrast, Bewegung, Helligkeit, Schärfe).
> `_energie` `[3.2]` ist der in Phase 3 neu berechnete Score aus der
> CLIP-Zero-Shot-Klassifikation. Die heuristische Formel dient dort nur noch
> als Fallback, falls kein Embedding vorliegt.

### Frage: „Aus welchen Daten besteht die finale Videodatei wirklich?"

> Die Pixel stammen ausschließlich aus den Original-Dateien `[1.1]`. Die
> gesamte Analyse (Generation 2) und der KI-Schnitt (Generation 3) erzeugen
> nur einen *Schnitt-Plan* — eine Liste von Ausschnitten und ihrer
> Reihenfolge. In Phase 4 liest FFmpeg die Originale erneut ein und setzt sie
> nach diesem Plan zusammen. Siehe Abschnitt 3.

---

*Stand: 2026-05-22. Direkt aus dem Quellcode rekonstruiert.*
*Teil der Bachelorarbeit CinAssist.*
