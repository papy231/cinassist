# Phase 3 — KI-Schnitt

> Wie aus den analysierten Szenen mehrerer Clips eine fertig geordnete
> Timeline entsteht — das Herzstück der Bachelorarbeit.

**Dauer:** Sekunden (ohne LLM-Verfeinerung) bis ~1 Minute (mit LLM).
**Charakter:** synchron, innerhalb eines FastAPI-Requests, aber rein
rechnerisch (keine Modell-Inferenz außer optionalem CLIP/LLM).
**Ergebnis:** eine `timelines`-Zeile in PostgreSQL mit einer geordneten
Liste von Video- und Audio-Segmenten, dazu drei quantitative
Qualitäts-Metriken.

**Quelldatei:** [`backend/api/ai.py`](../backend/api/ai.py) ·
Endpunkt `POST /api/ai/cut` · Funktion `ai_schnitt()` (Zeile 1886).

---

## 1. Zielsetzung dieser Phase

Phase 3 beantwortet die zentrale Frage der Arbeit:

> *Gegeben eine Menge analysierter Szenen aus einem oder mehreren Clips —
> welche Szenen kommen in die finale Timeline, und in welcher Reihenfolge?*

Das ist keine einzelne Berechnung, sondern eine **Kette von Entscheidungen**:
Auswahl (welche Szenen sind gut genug?), Teilung (sind Szenen zu lang?),
Klassifikation (welche Rolle spielt jede Szene?), Anordnung (welche
Reihenfolge erzählt am besten?) und Bewertung (wie gut ist das Resultat?).

Wichtig zur Abgrenzung: Phase 3 produziert **keine Pixel**. Sie erzeugt
einen **Schnitt-Plan** — eine Liste von Verweisen der Form *„nimm aus Clip X
die Sekunden a–b und setze sie an Position p"*. Die eigentlichen Bilder
werden erst in Phase 4 (Export) von FFmpeg aus den Originaldateien gelesen.

### Eine Designentscheidung vorweg: Determinismus

Zwei Optionen sind **standardmäßig deaktiviert**, beide aus demselben Grund —
**Reproduzierbarkeit**:

- **LLM-Verfeinerung** (`llm_aktiviert = False`): Würde ein externes
  Sprachmodell die Reihenfolge nachbearbeiten, hinge das Ergebnis vom
  jeweiligen Modell und dessen Zufalls-Sampling ab. Der Kern-Algorithmus
  ist dagegen **deterministisch**: gleiche Eingabe → gleiche Timeline.
- **Übergänge** (`mit_uebergaengen = False`): Crossfades im HTML5-Player
  verursachen sichtbares Stottern. Harte Schnitte sind technisch sauberer
  und entsprechen dem Standard im professionellen Schnitt.

Für die Verteidigung ist das ein starkes Argument: Der Algorithmus ist
nachvollziehbar und prüfbar, nicht von einer Black Box abhängig.

---

## 2. Auslöser und Eingabe

Der Nutzer wählt im Editor mehrere Clips, einen Stil und optional einen
Text-Prompt; das Frontend sendet einen `POST /api/ai/cut`. Der Request-Body
wird durch das Pydantic-Modell `AiCutRequest`
([ai.py:286](../backend/api/ai.py#L286)) validiert:

| Feld                 | Typ          | Default        | Bedeutung                                            |
| -------------------- | ------------ | -------------- | ---------------------------------------------------- |
| `stil`               | str          | `kinematisch`  | Schnitt-Stil (5 Optionen, siehe §4)                  |
| `clip_ids`           | list[str]    | —              | die zu verschneidenden Clips                         |
| `prompt`             | str \| None  | `None`         | optionale textliche Schnitt-Anweisung                |
| `provider`           | enum         | `ollama`       | LLM-Provider für die optionale Verfeinerung          |
| `llm_aktiviert`      | bool         | `False`        | LLM-Verfeinerung an/aus                              |
| `max_szenen`         | int \| None  | `None`         | Obergrenze für die Szenenanzahl                      |
| `qualitaet_schwelle` | float        | `0.0`          | Mindest-Energie (0 = keine Filterung)                |
| `mit_uebergaengen`   | bool         | `False`        | Crossfade/Wipe zwischen Szenen einfügen              |
| `beat_sync`          | bool         | `False`        | Schnittgrenzen auf Musik-Beats ausrichten            |
| `beat_pro_segment`   | int          | `4`            | Beats pro Segment, wenn `beat_sync` aktiv            |

---

## 3. Die Pipeline im Überblick

```
   clip_ids  +  stil  +  (prompt)
        │
   ┌────┴──────────────────────────────────────────────────┐
   │  ai_schnitt()  —  FastAPI-Request                      │
   └────┬──────────────────────────────────────────────────┘
        │
        ├─ 1.  Szenen aus DB laden  (alle Felder aus Phase 2)
        ├─ 2.  _energie zuweisen   (CLIP Zero-Shot »action vs calm«)
        ├─ 2a. Prompt encodieren   (CLIP-Text-Encoder, optional)
        ├─ 2b. Qualitäts-Schwelle  (schwache Szenen entfernen)
        ├─ 2c. Filler-Filter       (Endcards / »Subscribe« entfernen)
        ├─ 3.  Szenen unterteilen  (audio-bewusst, an Sprechpausen)
        ├─ 4.  Rollen zuweisen     (A/B-Roll  +  kinematische Rolle)
        ├─ 5.  Multicam-Dedup      (gleiche Kamera-Winkel zusammenfassen)
        ├─ 6.  SEQUENZ-ALGORITHMUS — einer von drei Pfaden:
        │        • Prompt-Pfad : MMR-Re-Ranking
        │        • Bogen-Pfad  : kinematischer Bogen + BEAM SEARCH
        │        • Einfach-Pfad: Energie-/Dialog-Sortierung
        ├─ 7.  LLM-Verfeinerung   (optional, Multi-Provider)
        ├─ 8.  Phantom-Schnitte verschmelzen
        ├─ 9.  Timeline-Segmente bauen  (Spur V1 + Spur A1)
        ├─ 10. Evaluations-Metriken berechnen
        └─ 11. Timeline in DB speichern  →  Antwort an den Browser
```

---

## 4. Die fünf Schnitt-Stile

Jeder Stil ist ein Parametersatz in `STIL_CONFIG`
([ai.py:221](../backend/api/ai.py#L221)):

| Stil           | min/max Dauer | tempo | dialog_gewicht | arc  | Charakter                                  |
| -------------- | ------------- | ----- | -------------- | ---- | ------------------------------------------ |
| `kinematisch`  | 1,5 – 12 s    | 0,60  | 0,30           | ✔    | narrativer Bogen, ausgewogene Dramaturgie  |
| `dokumentar`   | 3 – 40 s      | 0,25  | 0,85           | ✘    | chronologisch, dialogbetont, ruhig         |
| `werbespot`    | 0,8 – 5 s     | 0,90  | 0,10           | ✘    | kurze, energetische Schnitte               |
| `kurzfilm`     | 2 – 20 s      | 0,45  | 0,55           | ✔    | ausgewogenes Tempo, narrativer Fokus       |
| `social_media` | 0,5 – 3,5 s   | 0,95  | 0,05           | ✘    | sehr kurz, maximale Energie, kein Dialog   |

- **`tempo`** (0 = langsam, 1 = schnell) steuert, ob lange Szenen gekürzt
  werden.
- **`dialog_gewicht`** steuert, wie stark Dialog-Szenen bevorzugt werden.
- **`arc`** entscheidet, ob der dramatische Bogen-Algorithmus läuft oder
  ein einfacher Sortier-Algorithmus.
- Jeder Stil definiert außerdem **Übergänge** je Rolle (z. B. langsames
  `fade` für die Ouverture, `fadeblack` für die Cloture) — sie werden nur
  angewandt, wenn `mit_uebergaengen = True`.

---

## 5. Schritt-für-Schritt-Ablauf

### Schritt 1 — Szenen aus der Datenbank laden

Für jede `clip_id` werden der `Clip` und seine `szenen` geladen
([ai.py:1934](../backend/api/ai.py#L1934)) und in Python-Dictionaries
übersetzt. Jedes Szenen-Dict trägt **alle Analyse-Ergebnisse aus Phase 2**:
`embedding`, `beschreibung`, `transkription`, `transkription_json`,
`analyse_visuelle` sowie die relative Position `_pos_pct` im Quell-Clip.

Phase 3 beginnt also dort, wo Phase 2 aufgehört hat — sie liest **nur die
Datenbank**, nicht das Video.

### Schritt 2 — Energie-Score je Szene

Funktion `_szene_energie()` ([ai.py:337](../backend/api/ai.py#L337)). Jede
Szene erhält einen Score `_energie` ∈ [0, 1].

> **🔬 Deep dive — CLIP Zero-Shot statt Magic-Number-Formel**
> Dies ist die wichtigste methodische Verbesserung der Arbeit. Die **frühere**
> Energie kam aus der heuristischen Formel von Phase 2
> (`kontrast·0.40 + mouvement·0.35 + …`) mit nicht validierten
> Koeffizienten. Phase 3 berechnet den Score nun **anders**:
>
> ```
> avg_action = Ø cos(Szenen-Embedding, "action"-Prompts)
> avg_calm   = Ø cos(Szenen-Embedding, "calm"-Prompts)
> score      = 0.5 + 2.0 · (avg_action − avg_calm),  geclamped auf [0, 1]
> ```
>
> Es werden vordefinierte Text-Prompts (z. B. „a fast-paced action shot"
> vs. „a calm static scene") einmalig mit dem CLIP-Text-Encoder zu
> Vektoren gemacht (`backend/data/prompt_embeddings.json`). Der Score einer
> Szene ist dann, **wie viel näher ihr Bild-Embedding den Action-Prompts
> liegt als den Calm-Prompts** — eine klassische **Zero-Shot-Klassifikation**
> (Radford et al., ICML 2021).
>
> **Vorteil für die Verteidigung:** keine willkürlichen Koeffizienten mehr.
> Alle Werte stammen aus dem gemeinsamen Bild-Text-Vektorraum von CLIP, die
> Prompts sind explizit und auditierbar, das Ergebnis ist deterministisch.
> Die alte heuristische Formel existiert nur noch als **Fallback**, falls
> die Prompt-Embeddings fehlen oder eine Szene kein Bild-Embedding hat.

### Schritt 2a — Den Nutzer-Prompt encodieren

Hat der Nutzer einen Text eingegeben, wird dieser zur Laufzeit mit dem
**CLIP-Text-Encoder** in einen 512-dim-Vektor übersetzt
(`_encode_prompt`, [ai.py:141](../backend/api/ai.py#L141)). Dann wird für
jede Szene die Kosinus-Ähnlichkeit zwischen Bild-Embedding und Prompt-Vektor
berechnet und als `_prompt_relevance` gespeichert.

> **🔬 Deep dive — Warum das die eigentliche Stärke von CLIP ist**
> CLIP bildet **Bilder und Texte in denselben Vektorraum** ab. Genau das
> erlaubt es, einen frei eingegebenen Satz wie *„zeige die ruhigen
> Naturaufnahmen"* direkt mit Bildern zu vergleichen, **ohne ein eigenes
> Modell zu trainieren**. Der Prompt definiert die Absicht des Nutzers; die
> Relevanz-Scores ranken alle Szenen danach. Liegt ein Prompt vor, schaltet
> Phase 3 auf den **Prompt-Pfad** um (Schritt 6).

### Schritt 2b/2c — Qualitäts- und Filler-Filter

- **Qualitäts-Schwelle:** Ist `qualitaet_schwelle > 0`, werden Szenen mit
  `_energie` unter der Schwelle entfernt.
- **Filler-Filter** (`_ist_filler_szene`,
  [ai.py:1610](../backend/api/ai.py#L1610)): Szenen, deren LLaVA-Beschreibung
  oder Transkription Schlüsselwörter wie `subscribe`, `follow us`,
  `end card`, `abspann` enthält, sind Social-Media-Endcards oder Titeltafeln
  — **kein inhaltliches Material**. Sie werden ausgeschlossen, damit z. B.
  ein „FOLLOW US"-Outro nicht in der Timeline landet.

### Schritt 3 — Szenen unterteilen (audio-bewusst)

Funktion `_subdivise_scenes()` ([ai.py:590](../backend/api/ai.py#L590)).
Eine Szene, die deutlich länger als die Stil-Zieldauer ist (z. B. eine
40-Sekunden-Einstellung bei `kinematisch` mit Ziel ~4 s), wird in mehrere
Sub-Szenen geteilt.

> **🔬 Deep dive — Schnitte in Sprechpausen statt mitten ins Wort**
> Würde man eine lange Szene einfach in gleich große Stücke zerschneiden,
> fiele ein Schnitt fast immer **mitten in ein gesprochenes Wort** — hörbar
> unangenehm. `_find_natural_cut_points`
> ([ai.py:542](../backend/api/ai.py#L542)) löst das mit den Wort-Zeitstempeln
> aus Phase 2:
> 1. Aus `transkription_json` werden alle **Sprechpausen > 300 ms**
>    (Lücken zwischen Whisper-Segmenten) ermittelt.
> 2. Jeder ideale Schnittpunkt wird auf die **nächstgelegene Pause**
>    verschoben, sofern diese höchstens 1,5 s entfernt ist.
> 3. Gibt es keine Transkription, bleibt es bei gleichmäßiger Teilung.
>
> Zusätzlich bekommen die Sub-Szenen ein **Sinus-Energieprofil** (Anfang
> und Ende ruhig, Mitte energetisch) — eine Mini-Dramaturgie innerhalb der
> geteilten Szene.

### Schritt 4 — Rollen zuweisen

Jede Szene erhält **zwei** Klassifikationen:

**(a) Narrativer Typ** — `_detecte_role_narratif`
([ai.py:497](../backend/api/ai.py#L497)), angelehnt an die Fachbegriffe des
Filmschnitts:

| Typ            | Bedeutung                       | Signale                                       |
| -------------- | ------------------------------- | --------------------------------------------- |
| `a_roll`       | Hauptaufnahme (Sprecher, Interview) | Transkription vorhanden + geringe Bewegung |
| `b_roll`       | Schnittbild (Umgebung, Action)  | kein Dialog + mittlere/hohe Bewegung          |
| `establishing` | Einführungsaufnahme (Ort)       | hell + weit + ruhig + lang                    |

**(b) Kinematische Rolle** — `_rolle_kinematisch`
([ai.py:665](../backend/api/ai.py#L665)): `ouverture`, `action`,
`transition`, `climax` oder `cloture`. Sie ergibt sich aus Energie,
Bewegung, Kontrast, Farbtemperatur, Dauer, Dialog-Vorhandensein, der
relativen Position im Clip **und** dem narrativen Typ (A-Roll wird z. B.
bevorzugt zur `transition`, Establishing zur `ouverture`).

Anschließend werden Szenen unterhalb der Stil-Mindestdauer (`min_dauer`)
als Kandidaten ausgeschlossen.

### Schritt 5 — Multicam-Dedup

Funktionen `_get_multicam_groups` / `_dedupe_multicam_candidates`
([ai.py:2497](../backend/api/ai.py#L2497) ff.). Wenn mehrere hochgeladene
Clips **dieselbe Szene aus verschiedenen Kamerawinkeln** zeigen, würde die
Timeline denselben Moment dreifach enthalten. Der Dedup verhindert das.

> **🔬 Deep dive — Multicam-Erkennung über Audio + Bild**
> Zwei Clips gehören zur selben Multicam-Gruppe, wenn sie sich **sowohl
> akustisch als auch visuell** ähneln:
> - **Audio:** `_audio_chroma_correlation` vergleicht die 12-dimensionalen
>   **Chroma-Features** (Tonhöhen-Verteilung) beider Tonspuren via librosa.
>   Chroma ist robust gegen Klangfarbe und eignet sich für Musik/dieselbe
>   Performance.
> - **Bild:** `_visual_clip_similarity` vergleicht die CLIP-Embeddings der
>   Szenen beider Clips (mittlere maximale Kosinus-Ähnlichkeit).
>
> Überschreiten beide Maße ihre Schwellen
> (`visual ≥ 0.85 und audio ≥ 0.65`), werden die Clips per **Union-Find**
> (Disjoint-Set-Datenstruktur) zu einer Gruppe verschmolzen. Danach behält
> `_dedupe_multicam_candidates` pro 6-Sekunden-Zeitfenster nur **eine**
> Kamera — und zwar bewusst die, die zuletzt **am wenigsten** verwendet
> wurde. So entsteht ein natürlicher Kamerawechsel, genau wie ihn ein
> menschlicher Multicam-Cutter machen würde.

### Schritt 6 — Der Sequenz-Algorithmus (drei Pfade)

Hier entsteht die eigentliche Reihenfolge. Welcher Pfad läuft, hängt von
Prompt und Stil ab.

#### Pfad A — Prompt-getrieben (wenn ein Prompt vorliegt)

Aus den Kandidaten werden die relevantesten Szenen per **MMR** ausgewählt
([ai.py:2100](../backend/api/ai.py#L2100)).

> **🔬 Deep dive — Maximal Marginal Relevance (Carbonell & Goldstein, 1998)**
> Ein naives „nimm die Top-K nach Relevanz" hätte einen Fehler: Die
> ähnlichsten Szenen zum Prompt sind oft **untereinander** sehr ähnlich —
> die Timeline würde monoton. MMR balanciert Relevanz gegen Diversität:
> ```
> MMR(s) = λ · sim(s, Prompt) − (1−λ) · max sim(s, bereits_gewählt)
> ```
> Mit `λ = 0.7`: 70 % Relevanz zum Prompt, 30 % Strafe für Ähnlichkeit zu
> bereits gewählten Szenen. So ist jede ausgewählte Szene relevant **und**
> bringt visuelle Abwechslung. Danach wird je nach Stil entweder ein
> dramatischer Bogen erzwungen (`_zwinge_narrativen_bogen`) oder
> chronologisch sortiert.

#### Pfad B — Kinematischer Bogen (Default für `kinematisch`/`kurzfilm`)

Funktion `_baue_kinematischen_bogen` ([ai.py:971](../backend/api/ai.py#L971)).
Die Szenen werden nach ihrer kinematischen Rolle in eine **aristotelische
Bogenstruktur** einsortiert:

```
[Ouverture] → [Steigende Handlung ≈25%] → [Atempause/Dialog ≈20%]
            → [Energie-Aufbau ≈25%] → [Höhepunkt 1–2] → [Cloture]
```

Die Restszenen, die in dieses Schema nicht direkt passen, werden per
**Beam Search** eingefügt — der algorithmische Kern der Arbeit.

> **🔬 Deep dive — Beam Search (Breite 3)**
> `_beam_fill` ([ai.py:784](../backend/api/ai.py#L784)) ist ein
> **heuristischer Suchalgorithmus**. Eine perfekte Anordnung von n Szenen
> zu finden hieße, n! Permutationen zu prüfen — bei 15 Szenen über eine
> Billion. Beam Search reduziert das drastisch:
>
> 1. Es werden gleichzeitig **3 konkurrierende Teil-Sequenzen** („Beams")
>    verfolgt.
> 2. Jeder Beam wird um jede mögliche nächste Szene erweitert; die
>    Erweiterung wird mit einem **lokalen Score** bewertet (visuelle
>    Diversität zum Vorgänger, A/B-Roll-Wechsel-Bonus, starker Bonus für
>    Clip-Wechsel, leichter Energie-Bonus).
> 3. Aus allen Erweiterungen werden die **3 global besten** behalten —
>    bewertet mit `_sequence_score`.
> 4. Wiederholung, bis alle Szenen platziert sind. Der beste Beam gewinnt.
>
> Komplexität: O(Breite · |Kandidaten|²) statt O(n!). Mit Breite 3 ist das
> bis ~30 Szenen praktikabel und liefert eine **deutlich** bessere Ordnung
> als gierige Einzelauswahl, ohne den Aufwand einer vollständigen Suche.
>
> **`_sequence_score`** ([ai.py:732](../backend/api/ai.py#L732)) bewertet
> eine vollständige Sequenz nach vier gewichteten Kriterien:
> ```
> 0.20 · mittlere Energie
> 0.30 · mittlere visuelle Diversität benachbarter Szenen
> 0.20 · A/B-Roll-Alternierungsrate
> 0.30 · Clip-Wechsel-Rate
> ```
> Die hohe Gewichtung des Clip-Wechsels ist Absicht: Die zentrale Aufgabe
> von CinAssist ist das **Verschneiden mehrerer Quellen** — drei Szenen aus
> demselben Clip hintereinander wären ein schlechter Schnitt.
>
> Nach dem Beam Search laufen noch mehrere **Korrektur-Durchläufe**: Cloture
> ans Ende setzen, nie 3 lange Szenen hintereinander (Rhythmus), zwei
> A-Roll-Szenen durch ein B-Roll dazwischen aufbrechen, strikte
> Clip-Alternierung (bei 2 Clips: ABAB…).

#### Pfad C — Einfacher Schnitt (`werbespot`, `social_media`, `dokumentar`)

`_baue_einfachen_schnitt` ([ai.py:1161](../backend/api/ai.py#L1161)): kein
Bogen. Bei hohem `dialog_gewicht` (Dokumentation) chronologisch sortiert;
sonst nach Energie absteigend. Anschließend ebenfalls Clip-Wechsel
bevorzugt.

### Schritt 7 — LLM-Verfeinerung (optional)

Ist `llm_aktiviert = True` und gibt es mindestens 3 Szenen, wird die
Reihenfolge einem **Sprachmodell** zur Optimierung vorgelegt
(`_llm_verfeinern`, [ai.py:1555](../backend/api/ai.py#L1555)).

> **🔬 Deep dive — Eine Provider-Abstraktion für vier LLMs**
> `_llm_call_async` ([ai.py:1210](../backend/api/ai.py#L1210)) ist eine
> einheitliche Schnittstelle vor vier Anbietern: **Claude** (Anthropic),
> **GPT-4o** (OpenAI), **Gemini** (Google) und **Ollama/LLaMA3** (lokal).
> Bei `provider = "auto"` wird der erste verfügbare gewählt (Reihenfolge:
> Claude → OpenAI → Gemini → Ollama, je nach gesetztem API-Key). Alle
> Cloud-Aufrufe laufen direkt über `httpx`, ohne SDK — das hält die
> Abhängigkeiten klein und die HTTP-Schicht transparent.
>
> Dem Modell wird ein **strukturierter Prompt** mit allen Szenen-Metadaten
> übergeben (Energie, Bewegung, Kontrast, Rolle, Beschreibung, Dialog);
> Claude und GPT-4 erhalten zusätzlich einen Chain-of-Thought-Hinweis. Das
> Modell antwortet **nur mit einem JSON-Array von Szenen-Indizes**, z. B.
> `[2, 0, 5, 3, 1]`. `_parse_llm_response` extrahiert dieses Array robust
> (auch aus Reasoning-Text) und prüft es: Bei zu wenigen gültigen Indizes
> wird das LLM-Ergebnis **verworfen** und die algorithmische Reihenfolge
> behalten. Das LLM kann also nur verbessern, nie kaputtmachen.
>
> Deaktiviert per Default — siehe §1, Determinismus.

### Schritt 8 — Phantom-Schnitte verschmelzen

`_merge_kontinuierliche_szenen` ([ai.py:1628](../backend/api/ai.py#L1628)):
Folgen in der Reihenfolge zwei Szenen aus **demselben Clip**, die zeitlich
direkt aneinander anschließen (Lücke < 0,2 s), so ist ein Schnitt dazwischen
wirkungslos — der Zuschauer sieht durchlaufendes Material. Die beiden Szenen
werden zu einer verschmolzen.

### Schritt 9 — Timeline-Segmente bauen

`_baue_timeline` ([ai.py:1659](../backend/api/ai.py#L1659)) wandelt die
geordnete Szenenliste in konkrete **Timeline-Segmente** um. Pro Szene
entstehen **zwei** Segmente: eines auf der Videospur `v1`, ein gespiegeltes
auf der Audiospur `a1`.

Jedes Segment trägt: `start` (Position in der Timeline), `dauer`,
`mediaStart` (Startzeit im Quell-Clip), `clip_id`, `rolle`, `energie`,
eine Farbe (pro Clip eine andere — orange/blau/lila) und eine `groupId`,
die Video- und Audio-Segment koppelt.

Die `dauer` wird je nach `tempo` gekappt; bei aktivem `beat_sync` wird sie
zusätzlich auf das nächste Vielfache von N Beats gesnappt.

> **🔬 Deep dive — Beat-Sync (librosa, Ellis 2007)**
> Bei `beat_sync = True` wird die Tonspur des ersten („Master-")Clips per
> `librosa.beat.beat_track` analysiert — ein Verfahren aus Onset-Stärke und
> dynamischer Programmierung (Ellis, *„Beat Tracking by Dynamic
> Programming"*, 2007). Die erkannten Beat-Zeiten werden zu Timeline-
> Positionen: Jede Segmentgrenze springt auf den nächsten Beat. Ein
> rhythm-blinder Schnitt wirkt bei Musikmaterial immer leicht „off-time";
> Beat-Sync macht ihn taktgenau.

### Schritt 10 — Evaluations-Metriken

`_berechne_metriken` ([ai.py:1788](../backend/api/ai.py#L1788)) berechnet
**drei objektive Kennzahlen** für die erzeugte Sequenz:

| Metrik         | Bedeutung                                                      |
| -------------- | -------------------------------------------------------------- |
| `diversitaet`  | mittlerer CLIP-Kosinus-Abstand benachbarter Szenen (0–1)       |
| `wechselrate`  | Anteil der Übergänge mit Clip-Wechsel (0–1)                    |
| `dialog_treue` | Anteil der Schnitte, die **nicht** mitten in einem Wort liegen |

> **🔬 Deep dive — Warum diese Metriken wichtig sind**
> Die Arbeit kann (aus Zeitgründen) keine formale Nutzerstudie liefern.
> Diese drei Metriken sind der **quantitative Ersatz**: Sie machen die
> Schnittqualität **messbar und nachvollziehbar**, statt sie nur zu
> behaupten. `dialog_treue` prüft konkret, ob ein Schnittzeitpunkt strikt
> innerhalb des `(start, end)`-Intervalls eines Whisper-Worts liegt — eine
> direkte, objektive Messung der Audio-Sauberkeit. Diese Metriken sind ein
> wichtiges Gegenargument zur Kritik „keine Evaluation".

### Schritt 11 — Timeline speichern

Eine `Timeline`-Zeile wird in PostgreSQL angelegt
([ai.py:2247](../backend/api/ai.py#L2247)). Das Feld `daten` (JSON) enthält
alle Segmente, die Rollen-Verteilung, die Metriken, die verwendete
Scoring-Methode und die Beat-Sync-Infos. Die Antwort an den Browser enthält
`timeline_id`, Segmentzahl, Gesamtdauer, Metriken — der Editor lädt damit
die Timeline.

---

## 6. Datenzustand nach Phase 3

- **PostgreSQL — Tabelle `timelines`:** eine neue Zeile mit `name`, `stil`,
  `prompt`, `gesamtdauer` und dem vollständigen `daten`-JSON.
- **`clips` und `szenen`:** **unverändert** — Phase 3 liest nur, schreibt
  aber nichts an den Szenen.
- **Festplatte:** unverändert — es wurde noch keine Videodatei erzeugt.

Die Timeline ist ein reiner **Plan**. Erst Phase 4 macht daraus ein Video.

---

## 7. Kernfragen für die Verteidigung

**„Wie berechnest du, wie ‚gut' eine Szene ist — und sind das nicht
willkürliche Zahlen?"**
> Der Szenen-Score in Phase 3 stammt aus einer **CLIP-Zero-Shot-
> Klassifikation**: dem Vergleich des Bild-Embeddings gegen vordefinierte
> „action"- und „calm"-Text-Prompts. Es gibt dort keine frei gewählten
> Koeffizienten — alle Werte kommen aus dem gemeinsamen CLIP-Vektorraum und
> die Prompts sind explizit auditierbar. Die alte heuristische Formel ist
> nur noch Fallback.

**„Welcher Algorithmus ordnet die Szenen an?"**
> Ein **Beam Search** der Breite 3. Er verfolgt drei konkurrierende
> Teil-Sequenzen, bewertet jede Erweiterung lokal und global, und behält
> die besten — Komplexität O(Breite·n²) statt O(n!). Die globale
> Bewertungsfunktion gewichtet Energie, visuelle Diversität,
> A/B-Roll-Wechsel und Clip-Wechsel.

**„Wie verhinderst du Schnitte mitten im Wort?"**
> Lange Szenen werden audio-bewusst geteilt: Die Schnittpunkte werden auf
> Sprechpausen > 300 ms verschoben, die aus den Whisper-Wort-Zeitstempeln
> stammen. Die Metrik `dialog_treue` misst anschließend, wie gut das
> gelungen ist.

**„Was passiert, wenn ich einen Text-Prompt eingebe?"**
> Der Prompt wird mit dem CLIP-Text-Encoder in denselben 512-dim-Raum
> projiziert wie die Bilder. Jede Szene wird nach Kosinus-Ähnlichkeit zum
> Prompt geranked, und eine MMR-Auswahl balanciert Relevanz gegen
> Diversität.

**„Ist das Ergebnis reproduzierbar?"**
> Ja. Der Kern-Algorithmus ist deterministisch — gleiche Eingabe liefert
> dieselbe Timeline. Die LLM-Verfeinerung, die das aufweichen würde, ist
> per Default deaktiviert.

**„Wie evaluierst du die Schnittqualität ohne Nutzerstudie?"**
> Über drei objektive Metriken: visuelle Diversität (CLIP-Abstand),
> Clip-Wechselrate und Dialog-Treue. Sie ersetzen die fehlende Nutzerstudie
> nicht vollständig, liefern aber eine messbare, nachvollziehbare
> Selbstbewertung.

**„Wozu der Multicam-Dedup?"**
> Damit derselbe Moment, der aus mehreren Kamerawinkeln vorliegt, nicht
> mehrfach in der Timeline erscheint. Die Erkennung kombiniert Audio-Chroma-
> Korrelation und CLIP-Bildähnlichkeit; die Gruppierung erfolgt per
> Union-Find.

---

## 8. Bekannte Limitationen (ehrlich benannt)

- Die **Bogen-Proportionen** (25 % / 20 % / 25 % / 1–2 Climax / Cloture) und
  die Gewichte in `_sequence_score` sind heuristisch, nicht datenbasiert
  validiert.
- Die **Klassifikations-Schwellen** für A-Roll/B-Roll/Establishing sind
  empirisch gesetzt.
- Die Metriken sind eine **Selbstbewertung** — es fehlt ein Vergleich gegen
  einen menschlichen Schnitt und eine Nutzerstudie.
- `beat_sync` nimmt **homogenes** Musikmaterial an (eine Performance); für
  gemischtes Material wären Per-Clip-Beats nötig.

Diese Punkte sind bewusst dokumentiert und im Kapitel *Ausblick* der Arbeit
als Erweiterungen vorgesehen.

---

## 9. Zusammenfassung in einem Satz

> Phase 3 lädt die analysierten Szenen aus der Datenbank, bewertet jede per
> CLIP-Zero-Shot, teilt zu lange Szenen an Sprechpausen, klassifiziert sie
> nach narrativer und kinematischer Rolle, ordnet sie mit einem Beam Search
> (oder MMR bei Prompt) zu einer dramaturgisch sinnvollen Sequenz, baut
> daraus Video- und Audio-Segmente, misst die Qualität mit drei objektiven
> Metriken und speichert das Ganze als Timeline in PostgreSQL.

**→ Weiter mit [`PHASE_4_EXPORT.md`](PHASE_4_EXPORT.md).**

---

*Stand: 2026-05-22. Direkt aus dem Quellcode rekonstruiert.*
*Teil der Bachelorarbeit CinAssist.*
