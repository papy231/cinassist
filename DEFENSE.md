# CinAssist — Verteidigungsdokument

> Vorbereitung für das Gespräch mit dem Prüfer.
> Ziel: jede Designentscheidung wissenschaftlich begründen können.

---

## Inhaltsverzeichnis

1. [Die wissenschaftliche Fragestellung (30-Sekunden-Pitch)](#1-die-wissenschaftliche-fragestellung)
2. [Abgrenzung gegenüber DaVinci Resolve und Adobe Premiere](#2-abgrenzung-gegenüber-davinci-und-premiere)
3. [Die Pipeline — Wie der Schnitt tatsächlich entsteht](#3-die-pipeline)
4. [Wissenschaftliche Begründung jeder Designentscheidung](#4-wissenschaftliche-begründung-jeder-designentscheidung)
   - 4.1 PySceneDetect (Szenenerkennung)
   - 4.2 CLIP ViT-B/32 (visuelle Embeddings)
   - 4.3 Beam Search (Sequenz-Optimierung)
   - 4.4 Freytag-Pyramide (narrativer Bogen)
   - 4.5 Multimodale Fusion (Audio + Vision + Sprache)
5. [Ehrliche Limitationen](#5-ehrliche-limitationen)
6. [Alternative Open-Source-Werkzeuge](#6-alternative-open-source-werkzeuge)
7. [Vorbereitete Antworten auf erwartete Fragen](#7-vorbereitete-antworten)
8. [Literaturverzeichnis](#8-literaturverzeichnis)

---

## 1. Die wissenschaftliche Fragestellung

### Der 30-Sekunden-Pitch (auswendig lernen)

> *„Professionelle Schnittprogramme wie **DaVinci Resolve** und **Adobe Premiere** automatisieren **technische** Aufgaben: Farbkorrektur, Schnitterkennung, Transkription. Sie verstehen jedoch den **Inhalt** des Videomaterials nicht. Der Mensch entscheidet weiterhin, **welche Szene wann erzählt wird**.*
>
> *Meine wissenschaftliche Fragestellung lautet daher:*
> ***„Lassen sich drei Modalitäten — Bild (Vision), Ton (Audio) und Sprache (Language) — so kombinieren, dass ein System genug Inhaltsverständnis erlangt, um aus unmontiertem Rohmaterial automatisch einen kinematografischen Erzählbogen zu konstruieren?"***
>
> *CinAssist ist **kein Ersatz für den Editor**, sondern ein **Entscheidungsunterstützungssystem** (Decision Support System): es liefert einen ersten Schnittvorschlag, den der Editor validieren, anpassen oder verwerfen kann."*

### Der wissenschaftliche Beitrag (Contribution Statement)

Drei Beiträge:

1. **Eine multimodale Pipeline**, die drei Modalitäten in einem einzigen System verbindet:
   - **Audio**: Whisper (OpenAI/MLX) für Transkription mit Wort-Zeitstempeln
   - **Vision**: CLIP ViT-B/32 (OpenAI) für semantische Bild-Embeddings + PIL-basierte Pixelmetriken
   - **Sprache**: lokales LLaMA3 für Szenenbeschreibungen + Multi-Provider-LLM für Reihenfolge-Optimierung

2. **Eine automatische Szenenrollen-Klassifikation** im Kontext der Filmgrammatik:
   - A-Roll (Sprecher / Interview)
   - B-Roll (Schnittbild / Aktion)
   - Establishing Shot (Etablierungseinstellung)
   - Verbunden mit fünf kinematografischen Funktionen: Ouverture, Action, Transition, Climax, Cloture.

3. **Eine Beam-Search-basierte Sequenzkonstruktion** entlang der Freytag-Pyramide mit explizit traceable Entscheidungen — im Gegensatz zu einem End-to-End-Deep-Learning-Modell, das eine Blackbox wäre.

---

## 2. Abgrenzung gegenüber DaVinci und Premiere

### Die Vergleichstabelle

| Funktion                                              | DaVinci Resolve | Premiere Pro | **CinAssist** |
| :---------------------------------------------------- | :-------------: | :----------: | :-----------: |
| Schnitterkennung im fertigen Material (Scene Detect)  |       ✓         |      ✓       |      ✓        |
| Audiotranskription (Speech-to-Text)                   |       ✓ (paid)  |     ✓ (paid) |   ✓ (Whisper, lokal) |
| **Semantisches Verständnis pro Szene**                |       ✗         |      ✗       | **✓ (CLIP + LLM)** |
| **A-Roll / B-Roll / Establishing Klassifikation**     |       ✗         |      ✗       |    **✓**      |
| **Konstruktion eines narrativen Bogens**              |       ✗         |      ✗       | **✓ (Freytag-Pyramide)** |
| **Automatische Schnittgenerierung aus Rohmaterial**   |       ✗         |      ✗       |    **✓**      |
| LLM-basierte Sequenz-Verfeinerung                     |       ✗         |      ✗       |    **✓**      |
| Industrie-Standard für Farbgrading                    |   ✓ (Stärke)    |      ✓       |   ✗ (außerhalb des Scopes) |
| Multi-Cam-Synchronisation                             |       ✓         |      ✓       |   ✗ (außerhalb des Scopes) |

### Der entscheidende Satz (auswendig)

> *„DaVinci erkennt Schnitte in **bereits montiertem** Material. CinAssist nimmt **unmontiertes Rohmaterial** und **konstruiert** einen Schnitt. Das ist nicht passive Detektion, sondern aktive **Komposition**. Es ist ein fundamental anderes Problem."*

### Verwandte wissenschaftliche Arbeiten (Related Work)

- **Wang et al. (2019)**, *„Write-A-Video: Computational Video Montage from Themed Text"*, SIGGRAPH Asia 2019 — Text-gesteuerte automatische Videomontage. Verwandt, aber: textgetrieben, kein narrativer Bogen.
- **ByteDance AutoCut** (kommerziell, nicht peer-reviewed) — automatisches Schneiden von Vortrags-Videos. Nur ein Anwendungsfall.
- **Truong & Venkatesh (2007)**, *„Video Abstraction: A Systematic Review and Classification"*, ACM TOMM — Überblick über Video-Zusammenfassung. Bezug, aber Zusammenfassung ≠ Komposition.
- **Leake et al. (2017)**, *„Computational Video Editing for Dialogue-Driven Scenes"*, SIGGRAPH 2017 — Dialogszene-Optimierung. Sehr verwandt, aber nur für dialoglastige Szenen.

→ Keine dieser Arbeiten kombiniert alle drei Modalitäten in einer lokalen, vollständig open-source Pipeline mit explizit konstruiertem kinematografischen Bogen.

---

## 3. Die Pipeline

Die Pipeline besteht aus drei Phasen: **Ingestion** (Pro Upload), **KI-Schnitt** (Pro Anfrage) und **Export** (Pro Timeline).

### Phase A — Ingestion

Implementiert in `backend/workers/ingest.py`. Wird automatisch nach jedem Upload als Celery-Task gestartet. Sieben Schritte:

| #  | Schritt                              | Werkzeug / Methode                                        | Ausgabe                                                |
| -- | ------------------------------------ | --------------------------------------------------------- | ------------------------------------------------------ |
| 1  | Metadaten lesen                      | `ffprobe`                                                 | Dauer, Auflösung, FPS, Codec                           |
| 2  | Audio extrahieren                    | FFmpeg → WAV 16 kHz Mono                                  | Audiospur für Whisper                                  |
| 3  | Transkription                        | `mlx-whisper` Modell `large-v3-turbo` (Apple Silicon)     | Text + Wort-Zeitstempel                                |
| 4  | Szenenerkennung                      | PySceneDetect `ContentDetector` (Schwelle = 27)           | Liste von (Start, Ende) pro Szene                      |
| 5  | Visuelle Multi-Frame-Analyse         | PIL — drei Frames pro Szene (25 %, 50 %, 75 %)            | Helligkeit, Kontrast, Farbtemperatur, Bewegung, Schärfe, Energie |
| 6  | CLIP-Embedding                       | `open-clip` ViT-B/32 (OpenAI, Mittelframe der Szene)      | 512-dim Vektor pro Szene                                |
| 7  | Szenenbeschreibung                   | LLaMA3 via Ollama (lokal)                                 | Ein deutscher Satz pro Szene                            |

Alle Ergebnisse werden in PostgreSQL in der Tabelle `szenen` gespeichert.

### Phase B — KI-Schnitt

Implementiert in `backend/api/ai.py`, Endpunkt `POST /api/ai/cut`. Zehn Stufen:

1. **Szenen aus der Datenbank laden** (inklusive aller in Phase A berechneten Metadaten).
2. **Energie pro Szene berechnen** mit der Formel
   $$E = 0{,}40 \cdot K + 0{,}35 \cdot M + 0{,}15 \cdot L + 0{,}10 \cdot S$$
   mit $K$ = Kontrast, $M$ = Bewegung, $L$ = Luminanz, $S$ = Schärfe.
3. **Qualitäts-Schwelle anwenden**: Szenen unterhalb eines benutzerdefinierten Energie-Schwellenwerts werden ausgeschlossen.
4. **Audio-bewusste Subdivision**: lange Szenen werden geteilt, wobei Schnittpunkte an Sprachpausen (> 300 ms aus Whisper-Timestamps) ausgerichtet werden — kein Schnitt mitten im Wort.
5. **Narrative Rollen-Klassifikation** (A-Roll / B-Roll / Establishing):
   - *A-Roll*: Transkription vorhanden **und** Bewegung < 0,65 (typisches Interview)
   - *Establishing*: kein Dialog **und** hell (Lum. > 0,52) **und** ruhig (Mot. < 0,38) **und** lang (≥ 3,5 s)
   - *B-Roll*: alle übrigen
6. **Kinematische Rolle pro Szene** (Ouverture / Action / Transition / Climax / Cloture) anhand von Position im Quellclip, Energie, Bewegung und Dialogpräsenz.
7. **Konstruktion des kinematografischen Bogens** in fünf proportionalen Phasen:
   ```
   [1× Ouverture] → [25 % Action] → [20 % Transition] → [25 % Aufbau] → [1–2× Climax] → [1× Cloture]
   ```
   Dies entspricht der **Freytag-Pyramide** (siehe §4.4).
8. **Beam Search** (Breite 3) zur Anordnung der verbleibenden Szenen.
   Bewertungsfunktion einer Sequenz $\sigma$:
   $$\text{Score}(\sigma) = 0{,}20 \cdot \overline{E}_\sigma + 0{,}30 \cdot D_\sigma + 0{,}20 \cdot A_\sigma + 0{,}30 \cdot C_\sigma$$
   mit $\overline{E}_\sigma$ = mittlere Energie, $D_\sigma$ = mittlere visuelle Diversität (CLIP-Kosinus-Abstand), $A_\sigma$ = A/B-Roll-Alternationsrate, $C_\sigma$ = Clip-Wechselrate.
9. **Post-Processing-Regeln** (mehrere Durchgänge):
   - keine drei aufeinanderfolgenden Szenen aus demselben Clip
   - keine drei aufeinanderfolgenden langen Szenen (> 6 s)
   - bricht jedes A-A-Paar mit einer B-Roll-Szene auf
10. **Optionale LLM-Verfeinerung** (Claude 3.5 / GPT-4o / Gemini 1.5 / lokales LLaMA3): die finale Sequenz wird an ein LLM übergeben, das die Reihenfolge gemäß narrativer Kohärenz neu vorschlagen kann. Die Antwort ist robust gegen Reasoning-Text, JSON-Objekte und unsauberes Format (siehe `_parse_llm_response`).

### Phase C — Export

FFmpeg schneidet jedes Segment per `-ss start -t duration`, wendet `xfade`-Übergänge an (Dissolve, Fade, FadeBlack, WipeLeft), mischt die Audiospuren und kodiert in H.264/AAC → MP4.

---

## 4. Wissenschaftliche Begründung jeder Designentscheidung

### 4.1 PySceneDetect (Szenenerkennung)

**Was wird genau berechnet?**

PySceneDetect verwendet den `ContentDetector`. Algorithmus:

1. Konvertiere jeden Frame in den **HSV-Farbraum** (Farbton, Sättigung, Helligkeit).
2. Berechne **frameweise die mittlere absolute Differenz** der drei HSV-Kanäle gegenüber dem vorherigen Frame.
3. Gewichteter HSV-Score: $\Delta_t = w_H \cdot \Delta H + w_S \cdot \Delta S + w_V \cdot \Delta V$.
4. Wenn $\Delta_t > \text{threshold}$ (Standard: 27,0), wird **ein harter Schnitt** detektiert.

**Warum HSV statt RGB?**

HSV trennt **Farbton** (H) von **Helligkeit** (V), was robuster gegen Belichtungsänderungen innerhalb einer Szene ist. Ein kurzer Lichtwechsel im RGB-Raum würde fälschlich als Szenenwechsel erkannt; im HSV-Raum bleibt der Farbton stabil.

**Warum threshold = 27?**

Das ist der **Standardwert der Bibliothek**, empirisch von den Autoren auf einer Vielzahl von Inhalten validiert. Niedrigere Werte (10–20) sind für Action-Filme mit schnellen Schnitten geeignet, höhere Werte (30–40) für ruhige Dokumentationen. **27 ist ein guter Mittelweg für gemischte Inhalte.**

**Wissenschaftliche Grundlagen:**

- **Lienhart, R. (2001)**, *„Reliable Transition Detection in Videos: A Survey and Practitioner's Guide"*, *International Journal of Image and Graphics*, 1(3): 469–486.
- **Pickering, M. & Rüger, S. (2003)**, *„Evaluation of Key Frame-Based Retrieval Techniques for Video"*, *Computer Vision and Image Understanding*, 92(1): 217–235.
- PySceneDetect: <https://www.scenedetect.com>, MIT-Lizenz, GitHub: Breakthrough/PySceneDetect.

**Alternative Bibliothek (Erwähnung wert):**

- **TransNetV2** (Souček & Lokoč, 2020) — Deep-Learning-basierte Shot Boundary Detection. **F1-Score auf ClipShots-Dataset: 95.9 %** vs. PySceneDetect ~88 %. Apache-2.0-Lizenz. In einer Version 2 austauschbar.
  *Referenz*: Souček, T. & Lokoč, J. (2020), *„TransNet V2: An Effective Deep Network Architecture for Fast Shot Transition Detection"*, arXiv:2008.04838.

---

### 4.2 CLIP ViT-B/32 (visuelle Embeddings)

**Was ist CLIP konkret?**

CLIP (*Contrastive Language–Image Pre-training*) ist ein 2021 von OpenAI veröffentlichtes neuronales Netz, das auf **400 Millionen Bild-Text-Paaren** aus dem Internet trainiert wurde. Es besteht aus zwei Encoder-Türmen:

- **Bild-Encoder**: Vision Transformer (ViT-B/32 = Basisgröße, 32×32-Patches) → 512-dim Vektor
- **Text-Encoder**: Transformer → 512-dim Vektor

**Wie funktioniert das Training?**

Kontrastives Lernen: für ein Batch von N Bild-Text-Paaren maximiert das Modell die Kosinus-Ähnlichkeit der N **korrekten** Paare und minimiert die Ähnlichkeit der N²−N **falschen** Paare.

Ergebnis: Bilder und Texte teilen sich **denselben 512-dim Vektorraum**. Ähnliche Bilder erhalten ähnliche Vektoren (Kosinus-Ähnlichkeit nahe 1).

**Wozu nutze ich CLIP in CinAssist?**

Pro Szene wird ein Frame extrahiert und durch ViT-B/32 in einen 512-dim-Vektor projiziert. Diese Vektoren werden:

1. **In Postgres gespeichert** (Spalte `szenen.clip_embedding`, Typ `ARRAY[Float]`).
2. **Zur Berechnung visueller Diversität** zwischen Szenen verwendet:
   $$D(a, b) = 1 - \cos(\mathbf{v}_a, \mathbf{v}_b) = 1 - \frac{\mathbf{v}_a \cdot \mathbf{v}_b}{\|\mathbf{v}_a\| \, \|\mathbf{v}_b\|}$$
   Hohe Diversität → starker visueller Kontrast → besserer kinematografischer Schnitt.

**Warum ViT-B/32 und nicht ViT-L/14?**

| Modell      | Parameter | Größe   | Genauigkeit (ImageNet zero-shot) |
| ----------- | --------- | ------- | -------------------------------- |
| ViT-B/32    | 87 M      | 151 MB  | 63,2 %                           |
| ViT-B/16    | 86 M      | 332 MB  | 68,6 %                           |
| ViT-L/14    | 304 M     | 890 MB  | 75,5 %                           |

**Begründung**: ViT-B/32 bietet das beste Verhältnis von Genauigkeit zu Laufzeit für eine lokale Anwendung auf Apple Silicon ohne dedizierte GPU. Für visuelle **Ähnlichkeit zwischen Szenen** (nicht Bildklassifikation) ist der relative Unterschied zu ViT-L/14 marginal.

**Wissenschaftliche Grundlagen:**

- **Radford, A. et al. (2021)**, *„Learning Transferable Visual Models From Natural Language Supervision"*, OpenAI, ICML 2021. arXiv:2103.00020.
- **Dosovitskiy, A. et al. (2021)**, *„An Image is Worth 16×16 Words: Transformers for Image Recognition at Scale"*, ICLR 2021 (Vision Transformer Grundlagenpaper).
- **Vaswani, A. et al. (2017)**, *„Attention Is All You Need"*, NeurIPS 2017 (Transformer Grundlage).

**Alternative Bibliothek (Erwähnung wert):**

- **DINOv2** (Meta, 2023) — selbstüberwachtes Vision-Modell ohne Text. Höhere Bild-Bild-Ähnlichkeitsgenauigkeit als CLIP, dafür **kein gemeinsamer Text-Raum**. Falls man ausschließlich visuelle Diversität braucht, wäre DINOv2 besser. CinAssist nutzt jedoch absichtlich CLIP, damit eine **zukünftige Erweiterung „Suche per Textprompt"** möglich ist.

---

### 4.3 Beam Search (Sequenz-Optimierung)

**Was ist Beam Search?**

Beam Search ist ein **heuristischer Such-Algorithmus**, der eine Mittelposition zwischen zwei Extremen einnimmt:

| Algorithmus      | Eigenschaft                                                    |
| ---------------- | -------------------------------------------------------------- |
| **Greedy Search**| Wählt bei jedem Schritt die **lokal beste** Option. Schnell, aber landet oft in einem lokalen Optimum. |
| **Exhaustive Search** | Probiert **alle** Permutationen. Garantiert das globale Optimum, aber unrealistisch ($n!$ Komplexität). |
| **Beam Search**  | Hält bei jedem Schritt die **Top-k** Kandidatensequenzen parallel. Findet ein „nahezu globales" Optimum in polynomieller Zeit. |

**In CinAssist**: $k = 3$ (Beam-Breite). Implementiert in `backend/api/ai.py:555`, Funktion `_beam_fill`.

**Algorithmus (Pseudocode):**

```
beams = [(start_sequence, remaining_scenes)]
while remaining_scenes nicht leer:
    next_beams = []
    for jeden (seq, rest) in beams:
        for jede candidate in rest:
            new_seq  = seq + [candidate]
            new_rest = rest \ {candidate}
            local_score = visual_diversity + ab_alternation + clip_alternation + energy
            speichere (new_seq, new_rest, local_score)
    sortiere alle nach Score
    behalte top k
    beams = top_k
return beste Sequenz nach global_score
```

**Warum keine Greedy-Lösung?**

Greedy maximiert nur die **nächste** Wahl. Beispiel: Wenn zwei Clips A und B vorliegen und Greedy beim ersten Schritt eine A-Szene wählt, dann immer die nächst-energetischste — kann es passieren, dass es **alle B-Szenen am Anfang verbraucht** und am Ende nur noch A-Szenen verbleiben. → Clip-Wechsel-Regel verletzt.

Beam Search behält **drei parallele Hypothesen** und kann „A nehmen, B aufheben für später" als bessere Sequenz im globalen Score erkennen.

**Wissenschaftliche Grundlagen:**

- **Lowerre, B. T. (1976)**, *„The HARPY Speech Recognition System"*, PhD-Dissertation, Carnegie Mellon University — die ursprüngliche Verwendung von Beam Search in der Spracherkennung.
- **Sutskever, I., Vinyals, O. & Le, Q. V. (2014)**, *„Sequence to Sequence Learning with Neural Networks"*, NeurIPS 2014 — Beam Search als Standard-Dekodierungsverfahren in neuronaler maschineller Übersetzung. Bestätigt: Beam Search mit kleinem $k$ (typisch 3–10) übertrifft Greedy fast immer.
- **Russell, S. & Norvig, P. (2020)**, *„Artificial Intelligence: A Modern Approach"*, 4. Auflage, Pearson — Standardlehrbuch, Beam Search in Kapitel 3 (Heuristische Suche).

**Komplexitätsanalyse:**

- Greedy: $O(n^2)$ ($n$ = Anzahl Szenen)
- Beam Search (Breite $k$): $O(k \cdot n^2)$ — bei $k = 3$ also nur dreimal so teuer wie Greedy.
- Exhaustive: $O(n!)$ — unrealistisch.

---

### 4.4 Freytag-Pyramide (narrativer Bogen)

**Wer war Freytag?**

**Gustav Freytag** (1816–1895), deutscher Schriftsteller und Literaturtheoretiker, veröffentlichte 1863 *„Die Technik des Dramas"*. Er analysierte darin die Struktur klassischer Dramen (Sophokles, Shakespeare, Schiller) und formulierte ein **Fünf-Akt-Modell**, das bis heute die Grundlage der Dramaturgie ist.

**Die fünf Akte der Pyramide:**

```
                                CLIMAX (Höhepunkt)
                                   /\
                                  /  \
                                 /    \
                                /      \
                               /        \
               STEIGENDE      /          \    FALLENDE
               HANDLUNG      /            \   HANDLUNG
                            /              \
                           /                \
                          /                  \
      EXPOSITION         /                    \     KATASTROPHE
   (Ouverture)          /                      \    (Cloture)
```

**Übersetzung auf CinAssist:**

| Freytag (1863)        | CinAssist             | Funktion                                                |
| --------------------- | --------------------- | ------------------------------------------------------- |
| Exposition            | Ouverture             | Den Zuschauer in die Welt einführen, Aufmerksamkeit gewinnen |
| Steigende Handlung    | Action / Aufbau       | Spannung graduell aufbauen, Energie steigern              |
| Höhepunkt             | Climax                | Maximale Energie, dramatischer Wendepunkt                |
| Fallende Handlung     | Transition            | Atempause, Reflexion, Dialog                              |
| Katastrophe / Auflösung | Cloture              | Ruhiger Abschluss, emotionale Erinnerung                  |

**Warum diese Struktur in einem Schnittprogramm?**

Filmwissenschaft (Bordwell & Thompson, Murch) belegt: **der menschliche Zuschauer erwartet narrative Strukturen.** Eine willkürlich angeordnete Szenenfolge wird als chaotisch oder langweilig empfunden, selbst wenn jede Einzelszene visuell gelungen ist.

Ein Schnitt entlang der Freytag-Pyramide ist daher **kein ästhetischer Kniff**, sondern eine **kognitive Anforderung** an dramaturgisches Erzählen.

**Wissenschaftliche Grundlagen:**

- **Freytag, G. (1863)**, *„Die Technik des Dramas"*, Hirzel, Leipzig.
- **Aristoteles (~335 v. Chr.)**, *„Poetik"* — die ursprüngliche Theorie von Anfang/Mitte/Ende.
- **Murch, W. (2001)**, *„In the Blink of an Eye: A Perspective on Film Editing"*, Silman-James Press — moderner Standardtext zum Filmschnitt. Murch nennt sechs Kriterien für einen Schnitt: Emotion (51 %), Story (23 %), Rhythm (10 %), Eye-trace (7 %), Two-dimensional plane (5 %), Three-dimensional space (4 %).
- **Bordwell, D. & Thompson, K. (2019)**, *„Film Art: An Introduction"*, 12. Auflage, McGraw-Hill — Standard-Lehrbuch der Filmwissenschaft.
- **Snyder, B. (2005)**, *„Save the Cat!"*, Michael Wiese Productions — moderne Anwendung der Freytag-Struktur auf Drehbücher.

---

### 4.5 Multimodale Fusion (Audio + Vision + Sprache)

**Warum drei Modalitäten und nicht eine?**

Ein einzelner Sensor kann nicht den vollen Inhalt einer Videoszene erfassen:

- **Nur Visuell (CLIP)**: erkennt nicht, *wer* spricht oder *was* gesagt wird.
- **Nur Audio (Whisper)**: erkennt nicht, *wie* die Szene aussieht oder ob jemand sichtbar ist.
- **Nur Sprache (LLM)**: erkennt nichts ohne Eingangsmodalität.

**CinAssist kombiniert** alle drei:

```
        AUDIO              VISION             SPRACHE
       (Whisper)            (CLIP)             (LLaMA3)
           │                  │                   │
           ▼                  ▼                   ▼
     Transkription      512-dim Vektor    Szenenbeschreibung
     (+ Wort-Timestamps) (+ PIL-Metriken)  (1 Satz)
           │                  │                   │
           └──────────────────┴───────────────────┘
                              │
                              ▼
                  ┌────────────────────────┐
                  │ A-Roll / B-Roll        │  ← Klassifikation
                  │ Energie / Rolle        │  ← Bewertung
                  │ Schnitt-Punkte         │  ← Audio-aware Subdivision
                  └────────────────────────┘
```

**Beispiel der Wechselwirkung:**

- Wenn Whisper Sprache erkennt **und** PIL geringe Bewegung misst → Wahrscheinlichkeit hoch, dass es sich um ein **Interview** handelt → Klassifikation als A-Roll.
- Wenn keine Sprache **und** hohe Bewegung **und** kurze Dauer → typischer **B-Roll-Action-Schuss** → Klassifikation als ACTION-Rolle im Bogen.

**Wissenschaftliche Grundlagen:**

- **Baltrušaitis, T., Ahuja, C. & Morency, L.-P. (2018)**, *„Multimodal Machine Learning: A Survey and Taxonomy"*, *IEEE TPAMI* 41(2): 423–443. Standardreferenz zur multimodalen Fusion.
- **Ramachandram, D. & Taylor, G. W. (2017)**, *„Deep Multimodal Learning: A Survey on Recent Advances and Trends"*, *IEEE Signal Processing Magazine* 34(6): 96–108.

---

## 5. Ehrliche Limitationen

Diese Liste **selber zu nennen** ist eine Verteidigungsstrategie: sie zeigt wissenschaftliche Selbstkritik und entwaffnet die offensichtlichen Angriffe.

### 5.1 Magic Numbers ohne formale Validierung

Die Koeffizienten der Energie-Formel (0,40 / 0,35 / 0,15 / 0,10), der Beam-Score-Funktion (0,20 / 0,30 / 0,20 / 0,30) und die Bogen-Proportionen (25 / 20 / 25 / 10 %) sind **heuristisch gewählt** und nicht durch eine User-Studie validiert. Dies ist die offensichtlichste Schwäche und sollte als **„nächster Schritt: Ablation Study + Nutzerbewertung"** im Limitations-Kapitel der Bachelorarbeit stehen.

### 5.2 Bewegungsschätzung ohne Optical Flow

Die `_pixel_diff` Funktion in `ingest.py:321` berechnet die mittlere absolute Pixeldifferenz zwischen drei 32×32-skalierten Frames. **Das ist kein Optical Flow.** Ein Optical-Flow-Verfahren (Farnebäck 2003, Lucas-Kanade 1981) wäre präziser, jedoch ca. 10× langsamer. → Trade-off zugunsten der Performance.

### 5.3 Schärfe-Score auf 32×32-Graustufenbild

Die Laplace-Varianz auf 32×32 ist eine Annäherung. Industrieller Standard: volle Auflösung mit OpenCV `cv2.Laplacian()`. → Schneller, aber weniger genau.

### 5.4 Keine systematische Evaluierung

Es gibt **kein Ground-Truth-Dataset** für automatische Filmschnitte. Daher kann CinAssist nicht gegen einen objektiven Standard gemessen werden. Mögliche zukünftige Evaluierung:
- **User Study** mit 20+ Probanden (5er-Likert-Skala für Kohärenz, Rhythmus, Emotion)
- **Ablation Study**: jede Komponente einzeln deaktivieren und Auswirkung messen
- **Vergleich mit menschlichem Editor** (qualitativer A/B-Test)

### 5.5 Sprachabhängigkeit

Whisper ist auf Deutsch (`language="de"`) festgelegt. Mehrsprachige Inhalte würden teilweise oder gar nicht transkribiert. → Geringfügig, aber dokumentierbar.

### 5.6 CLIP-Unterausnutzung

CLIP ist ein **multimodales** (Text + Bild) Modell, doch CinAssist nutzt aktuell nur den Bild-Encoder. Der Text-Encoder könnte für eine **Szene-Suche per Textprompt** (z. B. „zeige mir alle ruhigen Außenaufnahmen") verwendet werden. → Bewusstes zukünftiges Feature.

---

## 6. Alternative Open-Source-Werkzeuge

Falls der Prüfer fragt, ob es bessere Alternativen gibt: **ja, und ich kenne sie**. Diese Liste zeigt, dass die Wahl bewusst getroffen wurde.

### 6.1 Szenenerkennung

- **TransNetV2** (Souček & Lokoč, 2020) — Deep-Learning-SBD, F1 ≈ 96 %.
- **PySceneDetect** (gewählt) — klassisch, interpretierbar, sehr leichtgewichtig.

### 6.2 Bewegungsschätzung

- **OpenCV `cv2.calcOpticalFlowFarneback`** (Farnebäck, 2003) — präziser Dense Optical Flow.
- **OpenCV `cv2.calcOpticalFlowPyrLK`** (Lucas-Kanade, 1981) — Sparse Optical Flow, sehr schnell für Feature-Punkte.
- **RAFT** (Teed & Deng, ECCV 2020) — Deep-Learning-Optical-Flow, State-of-the-Art, aber GPU-intensiv.

### 6.3 Visuelle Embeddings

- **CLIP ViT-B/32** (gewählt).
- **DINOv2** (Meta, 2023) — höhere Bild-Bild-Ähnlichkeit.
- **SigLIP** (Google, 2023) — Verbesserung gegenüber CLIP.

### 6.4 Personenerkennung (für A-Roll-Detektion)

- **MediaPipe** (Google) — Apache-2.0, sehr schnell, Gesichts- und Posenerkennung.
- **YOLOv8** (Ultralytics) — General-Purpose-Objekterkennung.
- → Mögliche Erweiterung: A-Roll präziser klassifizieren mit „Gesicht erkannt + Gesicht zentriert".

### 6.5 Sprecher-Diarisierung

- **pyannote.audio** (Bredin et al., 2020) — Wer spricht wann? Verfeinert A-Roll-Klassifikation.
- **Silero VAD** — sehr leichtgewichtige Voice Activity Detection.

### 6.6 Vision-Language Captioning (Szenenbeschreibung)

- **LLaMA3 (text-only)** (gewählt) — Beschreibungen basieren nur auf Transkript + Dauer.
- **BLIP-2** (Salesforce, 2023) — direkt bildbasierte Beschreibungen, präziser.
- **LLaVA** (Liu et al., 2023) — multimodales LLM für Bild + Sprache, open source.

### 6.7 Semantische Szene-Ähnlichkeit

- **Sentence-Transformers** (Reimers & Gurevych, 2019) — Embedding der LLM-Beschreibungen für semantischen Vergleich.

---

## 7. Vorbereitete Antworten

### F1 — „Woher kommen die Gewichte 0,40 / 0,35 / 0,15 / 0,10 in der Energie-Formel?"

> *Die Gewichte sind **heuristisch gewählte Hyperparameter**, inspiriert von der Filmgrammatik nach Walter Murch (2001, „In the Blink of an Eye"). Murch nennt Kontrast und Bewegung als die zwei dominantesten visuellen Faktoren für die Aufmerksamkeit des Zuschauers. Luminanz und Schärfe sind sekundäre Qualitätsfaktoren. Eine **formale Validierung durch eine Nutzerbewertungsstudie** ist als Limitation in Kapitel 5.1 meiner Arbeit dokumentiert und für eine zukünftige Erweiterung vorgesehen.*

### F2 — „Warum kein End-to-End Deep-Learning-Modell?"

> *Drei Gründe: **Erstens**, es existiert kein annotiertes Trainingsdataset für „guten" kinematografischen Schnitt. **Zweitens**, ein heuristisches System ist **interpretierbar**: jede Schnittentscheidung lässt sich nachvollziehen, was wissenschaftlich wertvoll ist. **Drittens**, eine Anforderung der Arbeit ist die **lokale, GPU-freie Ausführbarkeit auf Apple Silicon** — ein End-to-End-Modell der notwendigen Größe wäre dort nicht praktikabel.*

### F3 — „Deine Bewegungsschätzung ist primitiv im Vergleich zu Optical Flow."

> *Korrekt und dokumentiert. Ich nutze eine 32×32-Pixeldifferenz, weil sie etwa 10× schneller ist als Farnebäcks Dense Optical Flow (2003), was bei N Szenen × 2–3 Frame-Vergleichen pro Szene relevant wird. **Für eine Version 2 ist die Migration auf `cv2.calcOpticalFlowFarneback` geplant**, sobald die Genauigkeit gegenüber der Pipeline-Geschwindigkeit höher priorisiert wird.*

### F4 — „DaVinci Resolve hat bereits Scene Detection."

> *Ja — DaVinci erkennt Schnitte im **fertig montierten Material**. Mein System macht das **Inverse**: es nimmt **unmontierte Rohclips** und **konstruiert** einen Schnitt mit narrativem Bogen, Rollenklassifikation und Reihenfolgeoptimierung. DaVinci hat keine Notion von A-Roll/B-Roll, keinen kinematografischen Bogen und keine Multi-Modal-Inhaltsanalyse. Es ist ein fundamental anderes Problem.*

### F5 — „Wo liegt dein wissenschaftlicher Beitrag genau?"

> *Drei Punkte: (1) eine **multimodale Pipeline**, die Audio (Whisper), Vision (CLIP) und Sprache (LLM) in einer **lokalen Open-Source-Architektur** verbindet. (2) eine **traceable** Klassifikation in der Logik der Filmgrammatik (A-Roll/B-Roll/Establishing × Ouverture/Action/Climax/Cloture). (3) eine **Beam-Search-basierte Sequenzkonstruktion** entlang der Freytag-Pyramide. Soweit mir bekannt, gibt es keine vergleichbare offene Lösung mit dieser Kombination.*

### F6 — „Warum CLIP und nicht ein neueres Modell wie DINOv2?"

> *CLIP wurde gewählt, weil es einen **gemeinsamen Vektorraum für Bild und Text** bietet. Dies erlaubt eine spätere Erweiterung der Anwendung um eine **textbasierte Szenensuche** ohne erneutes Training. DINOv2 ist für reine Bild-Bild-Ähnlichkeit präziser, hat aber **keinen Textraum**. CinAssist priorisiert daher Erweiterbarkeit gegenüber maximaler Genauigkeit.*

### F7 — „PySceneDetect Schwelle = 27 — empirisch ausgewählt?"

> *Es ist der von den Autoren der Bibliothek empfohlene Standard, validiert auf einer breiten Auswahl von Inhalten (Werbespots, Filme, Dokumentationen). Niedrigere Werte (10–20) sind für sehr schnell geschnittene Action-Inhalte gedacht, höhere Werte (30–40) für ruhige Inhalte. **27 ist ein Mittelweg für gemischtes Material.** Eine Anpassung per Inhaltstyp wäre eine einfache Erweiterung.*

### F8 — „Wie evaluierst du, ob dein Schnitt gut ist?"

> *Aktuell qualitativ — visuelle Inspektion der erzeugten Timeline und Rückmeldung von Test-Nutzern. **Eine formale Evaluierung ist Teil der Limitations** in Kapitel 5.4. Geplant für eine Erweiterung sind: (a) eine **Nutzerbewertungsstudie** mit 20+ Probanden auf einer 5er-Likert-Skala (Kohärenz, Rhythmus, narrative Klarheit), und (b) eine **Ablation Study**, bei der jede Pipeline-Komponente einzeln deaktiviert wird.*

### F9 — „Was ist Freytags Pyramide eigentlich?"

> *Ein dramaturgisches Strukturmodell aus „Die Technik des Dramas" (Gustav Freytag, 1863). Es teilt jede klassische Erzählung in fünf Akte: **Exposition** (Einführung), **steigende Handlung** (Aufbau), **Höhepunkt**, **fallende Handlung** (Reflexion) und **Katastrophe / Auflösung** (Schluss). Es geht zurück auf Aristoteles' „Poetik" und ist bis heute Grundlage der Drehbuch- und Filmtheorie (z. B. Bordwell & Thompson, „Film Art").*

### F10 — „Was unterscheidet deinen Beam Search von einem einfachen Sortieren nach Energie?"

> *Sortieren nach Energie ist **eindimensional** und ignoriert die Beziehung zwischen aufeinanderfolgenden Szenen. Mein Beam Search optimiert eine **vier-dimensionale Funktion**: mittlere Energie, visuelle Diversität zwischen Nachbarn (CLIP-Kosinus), A-Roll/B-Roll-Alternation und Clip-Wechsel-Rate. Eine reine Energiesortierung würde z. B. drei energetische Szenen vom gleichen Clip nacheinander platzieren — was im Schnitt grauenhaft wirkt. Beam Search verhindert das.*

---

## 8. Literaturverzeichnis

### Klassische Theorie und Filmgrammatik

- Aristoteles (~335 v. Chr.). *Poetik*.
- Freytag, G. (1863). *Die Technik des Dramas*. Hirzel, Leipzig.
- Murch, W. (2001). *In the Blink of an Eye: A Perspective on Film Editing* (2nd ed.). Silman-James Press.
- Bordwell, D. & Thompson, K. (2019). *Film Art: An Introduction* (12th ed.). McGraw-Hill.
- Snyder, B. (2005). *Save the Cat!: The Last Book on Screenwriting You'll Ever Need*. Michael Wiese Productions.

### Szenenerkennung und Shot Boundary Detection

- Lienhart, R. (2001). „Reliable Transition Detection in Videos: A Survey and Practitioner's Guide". *International Journal of Image and Graphics*, 1(3), 469–486.
- Pickering, M. & Rüger, S. (2003). „Evaluation of Key Frame-Based Retrieval Techniques for Video". *Computer Vision and Image Understanding*, 92(1), 217–235.
- Souček, T. & Lokoč, J. (2020). „TransNet V2: An Effective Deep Network Architecture for Fast Shot Transition Detection". arXiv:2008.04838.

### Visuelle Embeddings und Vision Transformers

- Radford, A. et al. (2021). „Learning Transferable Visual Models From Natural Language Supervision". *Proceedings of ICML 2021*. arXiv:2103.00020.
- Dosovitskiy, A. et al. (2021). „An Image is Worth 16×16 Words: Transformers for Image Recognition at Scale". *ICLR 2021*.
- Vaswani, A. et al. (2017). „Attention Is All You Need". *NeurIPS 2017*.
- Oquab, M. et al. (2023). „DINOv2: Learning Robust Visual Features without Supervision". arXiv:2304.07193.

### Optical Flow und Bewegungsschätzung

- Lucas, B. D. & Kanade, T. (1981). „An Iterative Image Registration Technique with an Application to Stereo Vision". *Proceedings of IJCAI 1981*.
- Farnebäck, G. (2003). „Two-Frame Motion Estimation Based on Polynomial Expansion". *Proceedings of SCIA 2003*.
- Teed, Z. & Deng, J. (2020). „RAFT: Recurrent All-Pairs Field Transforms for Optical Flow". *ECCV 2020*.

### Schärfe-Metrik

- Pech-Pacheco, J. L., Cristóbal, G., Chamorro-Martínez, J. & Fernández-Valdivia, J. (2000). „Diatom Autofocusing in Brightfield Microscopy: A Comparative Study". *Proceedings of ICPR 2000*, 314–317.

### Beam Search und Sequenzdekodierung

- Lowerre, B. T. (1976). *The HARPY Speech Recognition System* (PhD thesis). Carnegie Mellon University.
- Sutskever, I., Vinyals, O. & Le, Q. V. (2014). „Sequence to Sequence Learning with Neural Networks". *NeurIPS 2014*.
- Russell, S. & Norvig, P. (2020). *Artificial Intelligence: A Modern Approach* (4th ed.). Pearson.

### Sprache und LLMs

- Touvron, H. et al. (2023). „LLaMA: Open and Efficient Foundation Language Models". arXiv:2302.13971.
- OpenAI (2022). „Introducing Whisper". <https://openai.com/research/whisper>.
- Radford, A. et al. (2022). „Robust Speech Recognition via Large-Scale Weak Supervision". arXiv:2212.04356.

### Multimodale Fusion und Verwandte Arbeiten

- Baltrušaitis, T., Ahuja, C. & Morency, L.-P. (2018). „Multimodal Machine Learning: A Survey and Taxonomy". *IEEE TPAMI*, 41(2), 423–443.
- Ramachandram, D. & Taylor, G. W. (2017). „Deep Multimodal Learning: A Survey on Recent Advances and Trends". *IEEE Signal Processing Magazine*, 34(6), 96–108.
- Wang, M. et al. (2019). „Write-A-Video: Computational Video Montage from Themed Text". *ACM SIGGRAPH Asia 2019*.
- Leake, M., Davis, A., Truong, A. & Agrawala, M. (2017). „Computational Video Editing for Dialogue-Driven Scenes". *ACM SIGGRAPH 2017*.
- Truong, B. T. & Venkatesh, S. (2007). „Video Abstraction: A Systematic Review and Classification". *ACM Transactions on Multimedia Computing, Communications, and Applications*, 3(1).

### Bibliotheken und Werkzeuge

- PySceneDetect: <https://www.scenedetect.com>, MIT License.
- OpenAI Whisper: <https://github.com/openai/whisper>, MIT License.
- MLX-Whisper: <https://github.com/ml-explore/mlx-examples>, MIT License.
- open-clip-torch: <https://github.com/mlfoundations/open_clip>, MIT License.
- FFmpeg: <https://ffmpeg.org>, LGPL/GPL.
- Ollama: <https://ollama.com>, MIT License.

---

*Stand: 2026-05-21. Verteidigungsdokument für CinAssist Bachelorarbeit.*
