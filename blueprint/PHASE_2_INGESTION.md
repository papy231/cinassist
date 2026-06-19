# Phase 2 — Asynchrone Ingestion

> Was der Celery-Worker mit dem Video tut, nachdem Phase 1 den Auftrag in
> die Redis-Warteschlange gelegt hat — von den rohen Bytes bis zu vollständig
> analysierten, in der Datenbank gespeicherten Szenen.

**Dauer:** mehrere Minuten (abhängig von Videolänge und Modell-Geschwindigkeit).
**Charakter:** asynchron, sequenziell, in einem **separaten Prozess**
(Celery-Worker), nicht im FastAPI-Server.
**Ergebnis:** der Clip ist `status = "analysiert"`, N `szenen`-Zeilen liegen
in PostgreSQL — jede mit Transkription, visueller Analyse, CLIP-Embedding und
einer Beschreibung. Erst jetzt kann Phase 3 (KI-Schnitt) arbeiten.

**Quelldatei:** [`backend/workers/ingest.py`](../backend/workers/ingest.py) ·
Celery-Task `ingestion_pipeline()` (Zeile 967).

---

## 1. Zielsetzung dieser Phase

Phase 2 verwandelt eine **undurchsichtige Videodatei** in eine **strukturierte,
durchsuchbare Wissensbasis**. Vorher ist das Video für das System nur ein
Block aus Bytes; nachher ist es eine Liste von Szenen, von denen jede
beschrieben, transkribiert, vermessen und in einen Vektorraum eingebettet ist.

Die Phase hat vier Teil-Ziele:

1. **Technische Erfassung** — Metadaten lesen, eine browsertaugliche
   Vorschau (Proxy) erzeugen.
2. **Auditive Analyse** — die Tonspur in Text mit Zeitstempeln umwandeln
   (Transkription).
3. **Visuelle Analyse** — das Video in Szenen zerlegen und jede Szene
   messen (Helligkeit, Kontrast, Bewegung) sowie semantisch einbetten (CLIP).
4. **Sprachliche Anreicherung** — jede Szene in natürlicher Sprache
   beschreiben (Vision-LLM).

### Warum ein eigener Prozess?

Der Celery-Worker läuft als **separater Betriebssystem-Prozess**, gestartet
mit `celery -A backend.core.celery_app worker --pool=solo`. Das hat zwei
Gründe:

- **Isolation:** Stürzt die Analyse ab (z. B. weil ein Modell den Speicher
  sprengt), bleibt der FastAPI-Server unberührt und kann weiter Uploads
  annehmen.
- **`--pool=solo`:** Standardmäßig verteilt Celery Tasks per `fork()` auf
  Subprozesse. PyTorch mit Metal (Apple GPU) **überlebt einen `fork()`
  nicht** — der GPU-Kontext wird ungültig. `--pool=solo` führt Tasks im
  Hauptprozess des Workers aus, ohne Fork. Für einen Demonstrator mit einem
  Video zur Zeit ist das vollkommen ausreichend.

---

## 2. Auslöser

Der Celery-Worker lauscht permanent auf die Redis-Liste `celery`. Sobald
Phase 1 dort per `.delay()` eine Nachricht ablegt, entnimmt der Worker sie
(`BRPOP`), deserialisiert die Argumente und ruft die Funktion auf:

```python
ingestion_pipeline(clip_id="8c1a6add-…", job_id="b0e8f24c-…")
```

Die Task lädt zuerst den `Clip` aus der DB
([ingest.py:978](../backend/workers/ingest.py#L978)) und liest dessen
`dateipfad` — den Pfad zum Roh-Video aus Phase 1. Ab hier arbeitet sie
**rein sequenziell**: Jeder Schritt muss abgeschlossen sein, bevor der
nächste beginnt.

---

## 3. Übersicht — die Pipeline-Schritte

```
   ROH-VIDEO  (backend/uploads/{uuid}.mp4)
        │
   ┌────┴─────────────────────────────────────────────────┐
   │  ingestion_pipeline()  —  Celery-Worker, sequenziell  │
   └────┬─────────────────────────────────────────────────┘
        │
        ├─ Schritt 0  ffprobe        →  Metadaten              [ 2– 3 %]
        ├─ Schritt 1  FFmpeg         →  Proxy-Video 960p       [    4 %]
        ├─ Schritt 1b FFmpeg         →  Waveform-PNG           [    4 %]
        ├─ Schritt 1c FFmpeg         →  Thumbnail-Strip        [    4 %]
        ├─ Schritt 2  FFmpeg         →  Audio-Spur WAV         [ 5–10 %]
        ├─ Schritt 3  mlx-whisper    →  Transkription          [15–30 %]
        ├─ Schritt 4  PySceneDetect  →  Szenen + Thumbnails    [35–50 %]
        ├─ Schritt 5  PIL            →  Visuelle Analyse       [52–54 %]
        ├─ Schritt 6  open-clip      →  CLIP-Embeddings        [55–75 %]
        ├─ Schritt 7  LLaVA / LLaMA3 →  Szenen-Beschreibungen  [80–95 %]
        └─ Schritt 8  SQLAlchemy     →  Persistierung szenen   [97–99 %]
                                                              [    100 %]
   Permanent: jeder Schritt meldet Fortschritt per
   Redis Pub/Sub → WebSocket → Browser-Fortschrittsbalken.
```

Die Prozentzahlen sind die `fortschritt`-Werte, die per
`_update_job()` an die `jobs`-Tabelle **und** an den Redis-Kanal
`job:{job_id}` geschrieben werden.

---

## 4. Beteiligte Bibliotheken und Werkzeuge

| Werkzeug             | Kategorie        | Aufgabe in Phase 2                                              | Typ                |
| -------------------- | ---------------- | --------------------------------------------------------------- | ------------------ |
| **Celery**           | Task-Runner      | Führt `ingestion_pipeline` im Worker-Prozess aus                | Python-Lib         |
| **psycopg2-binary**  | DB-Treiber       | Synchrone Verbindung Worker ↔ PostgreSQL                        | Python-Lib         |
| **redis-py**         | Pub/Sub          | Publiziert Fortschritt auf Kanal `job:{id}`                     | Python-Lib         |
| **ffprobe**          | Medien-Inspektor | Liest Metadaten (Dauer, Auflösung, Codec)                       | externes Programm  |
| **FFmpeg**           | Medien-Werkzeug  | Proxy, Waveform, Strip, Audio-Extraktion, Frame-Extraktion      | externes Programm  |
| **mlx-whisper**      | Speech-to-Text   | Transkription mit Wort-Zeitstempeln, Apple-Silicon-optimiert    | Python-Lib + Modell|
| **librosa**          | Audio-Analyse    | RMS-Pegelmessung für die Stille-Vorprüfung                      | Python-Lib         |
| **PySceneDetect**    | Shot Detection   | Erkennt Szenenwechsel über HSV-Differenz                        | Python-Lib         |
| **OpenCV** (headless)| Computer Vision  | Dekodiert Frames für PySceneDetect (indirekt)                   | Python-Lib         |
| **PIL / Pillow**     | Bildverarbeitung | Pixel-Metriken: Helligkeit, Kontrast, Bewegung, Schärfe         | Python-Lib         |
| **open-clip-torch**  | Vision-Embedding | 512-dim CLIP-Embedding je Szene                                 | Python-Lib + Modell|
| **PyTorch**          | ML-Framework     | Rechen-Backend für CLIP, Apple-MPS-Beschleunigung               | Python-Lib         |
| **httpx**            | HTTP-Client      | Ruft Ollama (LLaVA / LLaMA3) lokal auf                          | Python-Lib         |
| **Ollama + LLaVA/LLaMA3** | Vision-/Text-LLM | Erzeugt eine sachliche Beschreibung je Szene                | externer Dienst    |

Ausführliche Begründung jeder Bibliothek:
[`PHASE_0_BIBLIOTHEKEN.md`](PHASE_0_BIBLIOTHEKEN.md).

---

## 5. Schritt-für-Schritt-Ablauf

### Schritt 0 — ffprobe liest die Metadaten

Funktion `_get_video_info()`
([ingest.py:93](../backend/workers/ingest.py#L93)). Das externe Programm
`ffprobe` wird per `subprocess.run` aufgerufen:

```python
ffprobe -v quiet -print_format json -show_format -show_streams video.mp4
```

ffprobe liest **nur den Datei-Header**, nicht den Bildinhalt, und liefert
JSON. Daraus werden vier Werte extrahiert: `dauer`, `aufloesung`
(`"{breite}x{hoehe}"`), `bildrate` und `codec`. Diese werden per
`UPDATE` in die `clips`-Zeile geschrieben.

> **🔬 Deep dive — Wie die Bildrate berechnet wird**
> ffprobe gibt die Bildrate als Bruch (`r_frame_rate`) zurück, z. B.
> `"24000/1001"` (= 23,976 fps, der NTSC-Standard). Der Code teilt Zähler
> durch Nenner:
> ```python
> num, den = fps_str.split("/")
> bildrate = round(float(num) / float(den), 2)
> ```
> Der Bruch existiert, weil viele Bildraten **keine ganzen Zahlen** sind.
> Ein einfaches `int()` würde 23,976 fps fälschlich als 24 oder 23 lesen
> und alle späteren Frame-Zeitberechnungen verfälschen.

### Schritt 1 — FFmpeg erzeugt das Proxy-Video

Ein Roh-Video kann 4K und mehrere GB groß sein — zu schwer, um im Browser
flüssig vorzuspielen. FFmpeg erzeugt eine leichte **Proxy-Version**
([ingest.py:1016](../backend/workers/ingest.py#L1016)):

```python
ffmpeg -y -i video.mp4
       -vf scale=960:-2          # max. 960 px, Seitenverhältnis erhalten
       -c:v libx264 -preset fast -crf 26
       -g 12 -keyint_min 12 -sc_threshold 0
       -c:a aac -b:a 128k
       -movflags +faststart
       proxies/{stem}_proxy.mp4
```

> **🔬 Deep dive — Die Keyframe-Einstellung `-g 12`**
> Ein HTML5-`<video>`-Element kann beim Spulen (Seek) nur zum nächsten
> **Keyframe** springen, nicht zu einem beliebigen Frame. Standardmäßig
> setzt FFmpeg alle 2–3 Sekunden einen Keyframe — beim Spulen entsteht so
> ein Versatz von bis zu 2 Sekunden. CinAssist erzwingt mit
> `-g 12 -keyint_min 12 -sc_threshold 0` einen Keyframe **alle 12 Frames**
> (≈ 0,5 s bei 24 fps). Dadurch ist das Spulen im Editor framegenau genug,
> auf Kosten einer minimal größeren Proxy-Datei. `-sc_threshold 0`
> deaktiviert zusätzliche szenenbasierte Keyframes, damit der Abstand
> konstant bleibt.
>
> **`scale=960:-2`:** Die Breite wird auf 960 px gesetzt, die Höhe (`-2`)
> automatisch so berechnet, dass das Seitenverhältnis stimmt **und** durch
> 2 teilbar ist (eine Anforderung von H.264). Bei Hochkant-Videos dreht der
> Code auf `scale=-2:960`.
> **`-movflags +faststart`:** verschiebt den Metadaten-Index (`moov`-Atom)
> an den Dateianfang, damit der Browser das Video abspielen kann, bevor es
> vollständig geladen ist.

### Schritt 1b — Waveform-PNG

```python
ffmpeg -y -i video.mp4
       -filter_complex showwavespic=s=1920x80:colors=#86efac
       -frames:v 1  proxies/{stem}_wf.png
```

Erzeugt ein einzelnes Bild der Audio-Wellenform (1920×80 px, hellgrün),
das die Timeline-UI als Overlay über Audio-Segmenten anzeigt.

### Schritt 1c — Thumbnail-Strip

```python
ffmpeg -y -i video.mp4
       -vf "fps={24/dauer},scale=80:45,tile=24x1"
       -frames:v 1 -q:v 5  proxies/{stem}_strip.jpg
```

Extrahiert **24 gleichmäßig verteilte Frames** und fügt sie per `tile`
zu einem horizontalen Streifen zusammen — die Vorschau-Leiste, die man in
DaVinci Resolve oder Premiere über jedem Video-Segment sieht. Die
`fps`-Rate ist so gewählt, dass über die gesamte Videodauer genau 24
Frames entstehen.

### Schritt 2 — Audio-Extraktion

Funktion `schritt_audio_extrahieren()`
([ingest.py:131](../backend/workers/ingest.py#L131)):

```python
ffmpeg -y -i video.mp4
       -vn                       # kein Video
       -acodec pcm_s16le         # PCM 16-bit, unkomprimiert
       -ar 16000                 # 16 kHz Abtastrate
       -ac 1                     # Mono
       temp/{uuid}_audio.wav
```

> **🔬 Deep dive — Warum genau 16 kHz Mono?**
> Whisper wurde auf **16 kHz Mono** trainiert und erwartet exakt dieses
> Format. Die Wahl ist physikalisch begründet:
> - Menschliche Sprache liegt unterhalb von ~8 kHz.
> - Das **Nyquist-Shannon-Abtasttheorem** verlangt eine Abtastrate von
>   mindestens dem Doppelten der höchsten Frequenz. 16 kHz deckt also
>   8 kHz mit Reserve ab.
> - **Mono** genügt, weil Sprache keine Stereo-Information trägt — ein
>   zweiter Kanal würde nur Datenmenge ohne Mehrwert erzeugen.
>
> `pcm_s16le` ist **unkomprimiertes** Audio: keine MP3-Artefakte, die die
> Spracherkennung stören könnten. Die WAV-Datei ist temporär — sie wird in
> [ingest.py:1098](../backend/workers/ingest.py#L1098) sofort nach der
> Transkription gelöscht.

### Schritt 3 — Transkription mit mlx-whisper

Funktion `schritt_transkription()`
([ingest.py:311](../backend/workers/ingest.py#L311)). Das Modell ist
`mlx-community/whisper-large-v3-turbo`.

```python
result = mlx_whisper.transcribe(
    audio_pfad,
    path_or_hf_repo=WHISPER_MODEL,
    language="de",
    word_timestamps=True,
)
```

Das Ergebnis ist ein Dictionary mit `text`, `sprache` und `segmente[]`.
Jedes Segment hat `start`, `end`, `text` und eine Liste von **Wörtern mit
eigenen Zeitstempeln**. Diese Wort-Stempel sind später entscheidend: Phase 3
nutzt sie, um Schnitte in Sprechpausen statt mitten ins Wort zu legen.

> **🔬 Deep dive — mlx-whisper statt Standard-Whisper**
> `mlx-whisper` ist eine für Apple Silicon optimierte Variante. Sie nutzt
> **MLX**, Apples ML-Framework, und damit den **Neural Engine** der
> M-Chips. Auf einem M3 Pro ist sie rund 3× schneller als die
> PyTorch-Standardvariante — relevant, weil die Transkription sonst der
> langsamste Schritt der Pipeline wäre.

> **🔬 Deep dive — Zwei Schutzmechanismen gegen erfundene Dialoge**
> Whisper ist berüchtigt dafür, bei **Stille** trotzdem Text zu
> „halluzinieren" — meist Pseudo-Untertitel aus seinen Trainingsdaten
> (`„Vielen Dank"`, `„Untertitel der Amara.org-Community"`,
> `„Musik Musik Musik"`). Ein erfundener Dialog würde später den
> Chat-Assistenten und die Metrik *Dialog-Treue* verfälschen. CinAssist
> wehrt das doppelt ab:
>
> 1. **Stille-Vorprüfung** (`_ist_audio_stille`,
>    [ingest.py:217](../backend/workers/ingest.py#L217)): librosa misst den
>    **RMS-Pegel** der Tonspur. Liegt er unter 0,005 (≈ −46 dBFS), gilt das
>    Audio als faktisch stumm und Whisper wird **ganz übersprungen**.
> 2. **Halluzinations-Filter** (`_ist_halluzination`,
>    [ingest.py:237](../backend/workers/ingest.py#L237)): Jedes Segment wird
>    gegen eine Liste bekannter Phrasen geprüft. Zusätzlich erkennt
>    `_ist_repetierte_halluzination` Wortwiederholungen (wenn ≥ 4 Wörter aus
>    nur 1–2 verschiedenen Tokens bestehen). Treffer werden verworfen.

### Schritt 4 — Szenenerkennung mit PySceneDetect

Funktion `schritt_szenen_erkennen()`
([ingest.py:414](../backend/workers/ingest.py#L414)):

```python
video = open_video(video_pfad)
scene_manager = SceneManager()
scene_manager.add_detector(ContentDetector(threshold=SCENE_THRESHOLD))  # 27.0
scene_manager.detect_scenes(video)
scene_list = scene_manager.get_scene_list()
```

Für **jede erkannte Szene** wird per FFmpeg ein Thumbnail aus der
Szenenmitte extrahiert (`scale=320:-1`, gespeichert in
`temp/thumbs_{clip_id}/szene_NNN.jpg`).

> **🔬 Deep dive — Wie `ContentDetector` entscheidet**
> Der Algorithmus vergleicht jedes Bild mit seinem Vorgänger:
> 1. Konvertierung **RGB → HSV** (Farbton, Sättigung, Helligkeit).
> 2. Mittlere absolute Differenz je Kanal über alle Pixel.
> 3. Score = Mittelwert der drei Kanal-Differenzen.
> 4. Score > **27,0** → Szenenwechsel.
>
> **Warum HSV statt RGB?** HSV trennt **Farbe** (H) von **Helligkeit** (V).
> Zieht eine Wolke vor die Sonne, ändert sich in RGB *alles* gleichzeitig
> → Fehlalarm. In HSV sinkt nur V, der Farbton H bleibt stabil → der Plan
> wird korrekt erhalten. Echte Schnitte hingegen ändern auch H drastisch.
>
> **Warum 27,0?** Das ist der **empirische Standardwert** der
> PySceneDetect-Autoren, validiert über ein breites Korpus aus Filmen,
> Werbung und Dokumentationen. CinAssist behält ihn bewusst bei, weil das
> getestete Material in die Kategorie „Standard" fällt. Ein adaptiver,
> aus der Score-Verteilung berechneter Schwellwert wäre eine dokumentierte
> mögliche Erweiterung.
>
> **Fallback:** Ist PySceneDetect nicht installiert, teilt der Code das
> Video gleichmäßig in ~4-Sekunden-Segmente
> ([ingest.py:487](../backend/workers/ingest.py#L487)). Erkennt der
> Detektor keine einzige Szene, wird das ganze Video als **eine** Szene
> behandelt.

### Schritt 5 — Visuelle Analyse mit PIL

Funktion `schritt_analyse_visuelle()`
([ingest.py:598](../backend/workers/ingest.py#L598)). Pro Szene werden
**drei Frames** extrahiert (bei 25 %, 50 %, 75 % der Szenendauer) und
sieben Metriken berechnet:

| Metrik         | Frame   | Berechnung                                                    |
| -------------- | ------- | ------------------------------------------------------------- |
| `luminosite`   | 50 %    | mittlere RGB-Helligkeit / (3·N·255), Bereich 0–1              |
| `temperature`  | 50 %    | Verhältnis Ø R / Ø B → `warm` / `neutral` / `kalt`           |
| `kontrast`     | 50 %    | Standardabweichung der Luminanz, normiert auf /80             |
| `schaerfe`     | 50 %    | Varianz des Laplace-Operators, normiert auf /600              |
| `qualitaet`    | 50 %    | `schaerfe × (1 − Belichtungs-Strafe)`                         |
| `mouvement`    | 25/50/75| mittlere Pixeldifferenz zwischen den drei Frames, ×2,5        |
| `energie`      | —       | gewichtete Summe (siehe unten)                                |

> **🔬 Deep dive — Die Energie-Formel und ihre Koeffizienten**
> ([ingest.py:700](../backend/workers/ingest.py#L700))
> ```
> energie = kontrast·0.40 + mouvement·0.35 + luminosite·0.15 + schaerfe·0.10
> ```
> Die vier Koeffizienten summieren sich zu 1,0 — es ist eine **gewichtete
> Mittelung**. Sie sind **heuristisch** gewählt, inspiriert von Walter
> Murchs Montage-Theorie (*„In the Blink of an Eye"*, 2001): Kontrast und
> Bewegung gelten dort als die stärksten visuellen Aufmerksamkeits­
> faktoren, Helligkeit und Schärfe als sekundär. Dass die Koeffizienten
> nicht empirisch auf einem Datensatz validiert sind, ist eine **bewusst
> dokumentierte Limitation**. Wichtig für die Verteidigung: In **Phase 3**
> wird der Szenen-Score *nicht* mehr aus dieser Formel berechnet, sondern
> aus einer CLIP-Zero-Shot-Klassifikation — diese heuristische `energie`
> dient dort nur noch als Fallback.
>
> **Warum die Luminanz-Koeffizienten 0,299 / 0,587 / 0,114?** Der Kontrast
> nutzt die **Norm ITU-R BT.601** zur Umrechnung von RGB in wahrgenommene
> Helligkeit. Das menschliche Auge ist am empfindlichsten für Grün (0,587),
> weniger für Rot, kaum für Blau. Dies ist keine willkürliche Wahl, sondern
> ein internationaler Standard.
>
> **Warum die Schärfe über den Laplace-Operator?** Der Laplace-Operator
> misst die zweite Ableitung der Helligkeit — also die Stärke der Kanten.
> Ein scharfes Bild hat ausgeprägte Kanten → hohe Varianz; ein unscharfes
> Bild hat weiche Übergänge → niedrige Varianz. Das ist eine etablierte
> Methode der Computer Vision (Pech-Pacheco et al., 2000).
>
> **Bekannte Limitation — `mouvement`:** Die Bewegung wird als Pixel­
> differenz auf 32×32-Bildern geschätzt, **nicht** als echter Optical Flow
> (Farnebäck, Lucas-Kanade). Bei statischer Kamera ist die Schätzung gut,
> bei schnellen Schwenks ungenau. Auch dies ist dokumentiert.

### Schritt 6 — CLIP-Embeddings

Funktion `schritt_clip_embeddings()`
([ingest.py:739](../backend/workers/ingest.py#L739)). Für jede Szene wird
der **Mittelpunkt-Frame** extrahiert und durch das Modell **ViT-B/32**
geschickt:

```python
device = "mps" if torch.backends.mps.is_available() else "cpu"
model, _, preprocess = open_clip.create_model_and_transforms(
    CLIP_MODEL, pretrained="openai", device=device)

image = preprocess(Image.open(frame_pfad)).unsqueeze(0).to(device)
with torch.no_grad():
    embedding = model.encode_image(image)
    embedding = embedding / embedding.norm(dim=-1, keepdim=True)  # L2-Norm
```

Das Ergebnis ist ein **512-dimensionaler, L2-normalisierter Vektor** je
Szene.

> **🔬 Deep dive — Was ein CLIP-Embedding ist und wozu es dient**
> CLIP (Radford et al., ICML 2021) wurde auf 400 Millionen Bild-Text-Paaren
> trainiert. Es bildet ein Bild auf einen Punkt in einem 512-dimensionalen
> Raum ab, in dem **semantisch ähnliche Bilder nahe beieinander liegen**.
> Man weiß nicht explizit, wofür jede Dimension steht — die Bedeutung ist
> gelernt. Wichtig ist nur die Eigenschaft: Ähnliche Inhalte → kleiner
> Kosinus-Abstand, verschiedene Inhalte → großer Abstand.
>
> Die **L2-Normalisierung** (Teilen durch die Vektorlänge) ist notwendig,
> damit später nur der **Winkel** zwischen zwei Vektoren zählt, nicht ihre
> Länge — die Voraussetzung für die Kosinus-Ähnlichkeit.
>
> Verwendet wird das Embedding in Phase 3 gleich dreifach: für den
> Energie-Score (Zero-Shot „action vs. calm"), für die Prompt-Relevanz und
> für die Messung der visuellen Diversität zwischen Nachbarszenen.
>
> **Warum ViT-B/32 und nicht das größere ViT-L/14?** ViT-B/32 (87 Mio.
> Parameter, 151 MB) ist rund 6× leichter als ViT-L/14 bei nur moderat
> geringerer Genauigkeit — der passende Kompromiss für lokale Ausführung
> auf einem Mac ohne dedizierte GPU.

### Schritt 7 — Szenen-Beschreibung mit LLaVA / LLaMA3

Funktion `schritt_szenen_beschreiben()`
([ingest.py:810](../backend/workers/ingest.py#L810)). Jede Szene wird in
2–3 sachlichen Sätzen beschrieben — über eine **zweistufige, genre­
agnostische Pipeline**:

**Primär — LLaVA:7b (Vision-Modell).** Das Szenen-Thumbnail wird
base64-kodiert an Ollama geschickt:

```python
httpx.post(f"{OLLAMA_BASE_URL}/api/generate", json={
    "model": "llava:7b",
    "prompt": VISION_PROMPT,
    "images": [img_b64],
    "options": {"temperature": 0.2, "num_predict": 220},
})
```

Der `VISION_PROMPT` verlangt explizit eine **streng sachliche** Beschreibung
(Bildinhalt, Bildausschnitt, Beleuchtung) und verbietet das Erfinden von
Emotionen, Handlung oder Dialog.

**Fallback — LLaMA3 (Text-Modell).** Schlägt LLaVA fehl (fehlendes
Thumbnail, Timeout), beschreibt LLaMA3 die Szene aus dem **Dialog-Text**.

> **🔬 Deep dive — Warum LLaVA primär, LLaMA3 nur Fallback?**
> LLaMA3 ist ein **reines Text-Modell** — es *sieht* das Bild nicht.
> Beschriebe es eine Szene, könnte es nur aus dem Dialog raten, was für
> stummes Material (B-Roll, Landschaften) nutzlos ist. **LLaVA** dagegen
> ist ein **multimodales Modell**: Es analysiert das Thumbnail direkt und
> liefert eine faktische Beschreibung des tatsächlich Sichtbaren — und das
> für **jedes Genre** (Musik, Sport, Interview, Doku). LLaMA3 bleibt als
> Sicherheitsnetz, damit nie eine Szene ganz ohne Beschreibung bleibt.
> `temperature=0.2` hält die Ausgabe nüchtern und reproduzierbar; ein hoher
> Wert würde das Modell zum „Ausschmücken" verleiten.
>
> Die Funktion `_normalize_llava` bereinigt die Ausgabe: LLaVA produziert
> oft Bullet-Listen (`* Person: …`, `* Framing: …`), die zu einem flüssigen
> Satz zusammengefügt und auf ~360 Zeichen am letzten Satzende gekürzt
> werden.

### Schritt 8 — Persistierung in PostgreSQL

Funktion-Abschnitt ab
[ingest.py:1113](../backend/workers/ingest.py#L1113). Für jede Szene wird
ein `Szene`-Objekt erstellt und der DB-Session hinzugefügt:

```python
szene = Szene(
    clip_id=clip_id,
    szenen_nr=…, start_zeit=…, end_zeit=…, dauer=…,
    thumbnail_pfad=…,
    clip_embedding=embeddings[i],          # Schritt 6
    beschreibung=beschreibungen[i],        # Schritt 7
    transkription=seg_text,                # Schritt 3, passendes Segment
    transkription_json=seg_json,           # Schritt 3, mit Wort-Stempeln
    analyse_visuelle=analyse_visuelle[i],  # Schritt 5
)
db.add(szene)
```

Anschließend wird `clip.status = "analysiert"` gesetzt und **alles in einer
Transaktion committed**.

> **🔬 Deep dive — Wie Transkription den Szenen zugeordnet wird**
> Whisper transkribiert das **gesamte** Video; PySceneDetect zerlegt es
> unabhängig davon in Szenen. Die Zuordnung erfolgt über **zeitliche
> Überlappung**: Ein Whisper-Segment gehört zu einer Szene, wenn
> `seg.start < szene.end_zeit` **und** `seg.end > szene.start_zeit`. So
> landet jeder gesprochene Satz bei der Szene, in der er zu hören ist.
> Spricht ein Satz über eine Szenengrenze hinweg, wird er beiden Szenen
> zugeordnet — eine bewusst gewählte, gutartige Doppelung.

---

## 6. Fortschrittsmeldung über Redis Pub/Sub

Nach **jedem** Schritt ruft der Code `_update_job()`
([ingest.py:42](../backend/workers/ingest.py#L42)) auf. Diese Funktion tut
zweierlei:

1. Schreibt `status`, `fortschritt` und `nachricht` in die `jobs`-Zeile
   (per psycopg2, synchron).
2. Publiziert dieselben Daten als JSON auf dem Redis-Kanal `job:{job_id}`.

Der WebSocket-Handler aus Phase 1 ist auf diesen Kanal abonniert und leitet
jede Nachricht an den Browser weiter — so bewegt sich der
Fortschrittsbalken. Zusätzlich überträgt `_update_job` optional ein
`schritt_daten`-Dict mit konkreten Belegen (z. B. Anzahl Segmente,
verwendeter Schwellwert), das die UI im Detail-Modal anzeigt.

---

## 7. Datenzustand nach Phase 2

### 7.1 Auf der Festplatte

```
backend/uploads/{uuid}.mp4              ← Original (unverändert)
backend/proxies/{uuid}_proxy.mp4        ← 960p-Vorschau
backend/proxies/{uuid}_wf.png           ← Waveform
backend/proxies/{uuid}_strip.jpg        ← Thumbnail-Streifen
backend/temp/thumbs_{clip_id}/szene_NNN.jpg  ← 1 Thumbnail je Szene
```

Die temporäre WAV-Audiodatei wurde **gelöscht**.

### 7.2 PostgreSQL — Tabelle `clips`

Die zuvor leeren Felder sind nun gefüllt: `dauer`, `aufloesung`,
`bildrate`, `codec`. Das Feld `status` steht auf `"analysiert"`.

### 7.3 PostgreSQL — Tabelle `szenen` (N Zeilen)

Pro Szene eine Zeile mit: `clip_id`, `szenen_nr`, `start_zeit`,
`end_zeit`, `dauer`, `thumbnail_pfad`, `clip_embedding` (512-dim),
`beschreibung`, `transkription`, `transkription_json`, `analyse_visuelle`.

### 7.4 PostgreSQL — Tabelle `jobs`

`status = "fertig"`, `fortschritt = 100`, `ergebnis` enthält ein
Zusammenfassungs-JSON (Szenenanzahl, Dauer, Auflösung, ob Embeddings
vorhanden).

---

## 8. Fehlerbehandlung und Fallback-Strategie

Phase 2 ist so gebaut, dass **ein einzelner fehlender Baustein nicht die
ganze Pipeline stoppt**. Jeder Schritt hat einen Fallback:

| Schritt          | Wenn das Werkzeug fehlt / fehlschlägt                          |
| ---------------- | -------------------------------------------------------------- |
| Proxy            | übersprungen — die UI nutzt dann das Original                  |
| Transkription    | leere Transkription, Pipeline läuft weiter                     |
| Stille erkannt   | Whisper wird übersprungen, kein Pseudo-Dialog                  |
| Szenenerkennung  | gleichmäßige Aufteilung in ~4-s-Segmente                       |
| Visuelle Analyse | Fallback-Werte (alle Metriken = 0,5)                           |
| CLIP-Embeddings  | Null-Vektoren `[0.0]·512`                                      |
| Beschreibung     | LLaVA → LLaMA3 → generischer Text `"Szene N: Xs"`              |
| Gesamter Task    | `status = "fehler"`, `clip.status = "fehler"`, Exception       |

> Schlägt die **gesamte** Task fehl (z. B. unlesbare Datei), fängt der
> `except`-Block in [ingest.py:1168](../backend/workers/ingest.py#L1168)
> die Exception, setzt Job und Clip auf `"fehler"` und meldet dies per
> Redis an den Browser.

---

## 9. Kernfragen für die Verteidigung

**„Warum läuft Phase 2 in einem eigenen Prozess?"**
> Isolation und Stabilität: Ein Absturz der Analyse darf den Web-Server
> nicht treffen. Außerdem erzwingt `--pool=solo`, dass PyTorch+Metal nicht
> über einen `fork()` gehen, den der Apple-GPU-Kontext nicht überlebt.

**„Warum wird die Tonspur in 16 kHz Mono extrahiert?"**
> Whisper ist auf genau dieses Format trainiert. 16 kHz erfüllt das
> Nyquist-Theorem für Sprache (< 8 kHz); Mono genügt, weil Sprache keine
> Stereo-Information trägt.

**„Wie verhinderst du, dass Whisper Dialoge erfindet?"**
> Zweistufig: eine RMS-Stille-Vorprüfung mit librosa überspringt Whisper
> bei faktisch stummen Clips ganz; ein Halluzinations-Filter verwirft
> bekannte Pseudo-Phrasen und repetitive Token-Wiederholungen.

**„Woher kommt der Schwellwert 27 bei der Szenenerkennung?"**
> Es ist der empirische Standardwert der PySceneDetect-Autoren, validiert
> über ein breites Korpus. Für das getestete Material angemessen; ein
> adaptiver Schwellwert wäre eine dokumentierte Erweiterung.

**„Wofür braucht man die CLIP-Embeddings?"**
> Sie sind die semantische Repräsentation jeder Szene. Phase 3 nutzt sie
> für den Energie-Score, die Prompt-Relevanz und die Messung der visuellen
> Diversität — alles über die Kosinus-Ähnlichkeit im 512-dim-Raum.

**„Warum LLaVA und nicht nur LLaMA3 für die Beschreibungen?"**
> LLaMA3 sieht das Bild nicht und müsste raten. LLaVA ist multimodal,
> analysiert das Thumbnail direkt und liefert eine faktische, genre­
> unabhängige Beschreibung. LLaMA3 bleibt nur als Fallback.

**„Was passiert, wenn ein Schritt fehlschlägt?"**
> Jeder Schritt hat einen definierten Fallback (siehe Abschnitt 8). Die
> Pipeline läuft mit reduzierter Information weiter, statt komplett
> abzubrechen.

---

## 10. Zusammenfassung in einem Satz

> Phase 2 nimmt das Roh-Video aus der Redis-Warteschlange, liest seine
> Metadaten, erzeugt eine Browser-Vorschau, transkribiert die Tonspur mit
> Whisper, zerlegt das Bild mit PySceneDetect in Szenen, vermisst jede Szene
> mit PIL, bettet sie mit CLIP in einen Vektorraum ein, beschreibt sie mit
> LLaVA und speichert schließlich alle Szenen samt ihrer Analyse in
> PostgreSQL — und meldet jeden Schritt live per WebSocket an den Browser.

**→ Weiter mit [`PHASE_3_KI_SCHNITT.md`](PHASE_3_KI_SCHNITT.md).**

---

*Stand: 2026-05-22. Direkt aus dem Quellcode rekonstruiert.*
*Teil der Bachelorarbeit CinAssist.*
