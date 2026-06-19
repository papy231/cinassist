# 07 — KI-Schnitt-Spezifikation

> Das ist das intellektuelle Herzstück der Arbeit. Quelle: `backend/api/ai.py`,
> Endpunkt `POST /api/ai/cut`. Wissenschaftliche Begründung jeder Wahl: `DEFENSE.md §4`.

## 7.1 Ziel

Aus den analysierten Szenen (`szenen`-Tabelle) eine **geordnete, narrativ kohärente
Timeline** konstruieren — deterministisch (`NFR-6`) und nachvollziehbar (`NFR-7`).

## 7.2 Eingabe / Ausgabe

- **Eingabe:** `AiCutRequest` (siehe `05_API §5.3`) + alle Szenen der `clip_ids` aus der DB.
- **Ausgabe:** `AiCutResult` mit gespeicherter Timeline + Qualitätsmetriken.

## 7.3 Verarbeitungsstufen (Soll, 10 Stufen)

| Stufe | Anforderung | Beschreibung |
|-------|-------------|--------------|
| 1 | AI-0 | **Szenen laden** inkl. Embedding, Transkription, `analyse_visuelle`. |
| 2 | AI-1 | **Score / Energie** pro Szene berechnen. |
| 3 | AI-2 | **Qualitätsschwelle** anwenden (`qualitaet_schwelle`). |
| 4 | AI-3 | **Audio-bewusste Subdivision** langer Szenen. |
| 5 | AI-4 | **Rollenklassifikation** A-Roll / B-Roll / Establishing. |
| 6 | AI-5 | **Kinematische Rolle** je Szene (Ouverture/Action/Transition/Climax/Cloture). |
| 7 | AI-6 | **Bogen-Konstruktion** (Freytag-Phasen). |
| 8 | AI-7 | **Beam Search** (Breite 3) zur Reihenfolge. |
| 9 | AI-8 | **Post-Processing-Regeln**. |
| 10 | AI-10 | **Optionale LLM-Verfeinerung** (Default aus). |
| — | AI-9 | **Metriken** berechnen und zurückgeben. |

## 7.4 AI-1 — Szenen-Score (Soll)

Das System MUSS zwei Scoring-Methoden unterstützen und die verwendete im Feld
`scoring_methode` ausweisen:

**(a) Primär — CLIP Zero-Shot** (`_szene_energie`): Kosinus-Ähnlichkeit des Szenen-Embeddings
zu Text-Prompt-Embeddings („action / dynamic motion" vs. „calm / static") liefert einen
Energie-Score 0..1. Begründung: ersetzt willkürliche Koeffizienten durch ein gelerntes Modell
(Radford et al. 2021).

**(b) Fallback — heuristische Formel** (wenn kein Embedding vorhanden):
```
Energie = 0.40·Kontrast + 0.35·Bewegung + 0.15·Luminanz + 0.10·Schärfe
```
> **Limitation (NFR-8):** Die Gewichte sind heuristisch (Murch 2001). Die Phase-2-Spalte
> `analyse_visuelle.energie` nutzt weiterhin diese Formel. Als Limitation dokumentiert
> (`DEFENSE.md §5.1`).

## 7.5 AI-4 — Rollenklassifikation (Soll)

| Rolle | Kriterium (Soll) |
|-------|------------------|
| **A-Roll** | Transkription vorhanden UND Bewegung < 0,65 (typisches Interview) |
| **Establishing** | kein Dialog UND hell (Lum > 0,52) UND ruhig (Bew < 0,38) UND lang (≥ 3,5 s) |
| **B-Roll** | alle übrigen |

Klassifikation MUSS allein aus berechneten Metriken erfolgen (kein trainiertes Modell →
Interpretierbarkeit, `NFR-7`).

## 7.6 AI-6 — Kinematografischer Bogen (Soll)

Zuordnung der Szenen zu fünf Funktionen nach Freytag (1863):

```
OUVERTURE  → visuell stark, Clip-Anfang, kein Dialog
ACTION     → dynamisches B-Roll, hoher Kontrast/Bewegung
TRANSITION → A-Roll, Dialog, narratives Bindeglied
CLIMAX     → maximale Energie + hohe Bewegung
CLOTURE    → ruhig, warm/neutral, Clip-Ende
```

Proportionaler Aufbau (Soll): `[1× Ouverture] → [25% Action] → [20% Transition] →
[25% Aufbau] → [1–2× Climax] → [1× Cloture]`.

## 7.7 AI-7 — Beam Search (Soll)

- Breite **k = 3** (nicht greedy, nicht vollständig → `O(k·n²)`).
- **Globale Bewertungsfunktion einer Sequenz σ:**
  ```
  Score(σ) = 0.20·mittlere_Energie
           + 0.30·visuelle_Diversität   (mittlerer CLIP-Kosinus-Abstand benachbarter Szenen)
           + 0.20·A/B-Alternationsrate
           + 0.30·Clip-Wechselrate
  ```
- Das System MUSS die global beste Sequenz zurückgeben, nicht die schrittweise beste.
- Begründung gegen Greedy: Greedy verbraucht z. B. alle B-Szenen früh → verletzt
  Clip-Wechsel-Regel (`DEFENSE.md §4.3`).

## 7.8 AI-8 — Post-Processing-Regeln (Soll, MUSS)

1. **Clip-Wechsel:** niemals drei Szenen desselben Quellclips in Folge (mehrere Durchgänge).
2. **Long/Short:** niemals drei lange Szenen (> 6 s) in Folge.
3. **A/B-Alternierung:** jedes A-A-Paar wird durch eine B-Roll-Szene getrennt.

## 7.9 AI-3 — Audio-bewusste Subdivision (Soll)
Lange Szenen MÜSSEN vor der Anordnung geteilt werden; Schnittpunkte MÜSSEN an
Whisper-Sprechpausen (> 300 ms) ausgerichtet werden — **kein Schnitt mitten im Wort**
(`FR-20`).

## 7.10 AI-22 — Prompt-gesteuerter Schnitt (Soll, optional)
Bei gesetztem `prompt` MUSS das System Szenen nach CLIP-Text-Ähnlichkeit zum Prompt
priorisieren und per **MMR** (Maximal Marginal Relevance) gegen Redundanz balancieren.
Metrik `prompt_relevance` MUSS dann zurückgegeben werden (`FR-22`).

## 7.11 AI-10 — LLM-Verfeinerung (Soll, optional, Default AUS)
- Aktiv nur bei `llm_aktiviert = true`.
- Provider-Priorität: Claude → OpenAI → Gemini → Ollama (lokal, immer verfügbar).
- Antwort-Parsing MUSS robust sein (Reasoning-Text, JSON-Modus, unsauberes Format → `NFR-10`).
- **Default aus**, um `NFR-6` (Determinismus) zu garantieren.

## 7.12 AI-9 — Qualitätsmetriken (Soll, MUSS)
Nach jedem Schnitt MUSS das System zurückgeben:

| Metrik | Definition | Wertebereich |
|--------|-----------|--------------|
| `diversitaet` | mittlerer CLIP-Kosinus-Abstand benachbarter Szenen | 0..1 (höher = vielfältiger) |
| `wechselrate` | Anteil der Schnitte mit Quellclip-Wechsel | 0..1 |
| `dialog_treue` | Anteil der Schnitte, die NICHT mitten im Wort liegen | 0..1 (höher = besser) |
| `prompt_relevance` | mittlere CLIP-Ähnlichkeit zum Prompt (nur bei Prompt) | 0..1 |
| `szenen_anzahl`, `uebergaenge` | Strukturzähler | int |

Diese Metriken adressieren die Kritik „keine Evaluierung" teilweise (`DEFENSE.md §5.4`).

## 7.13 Magic-Number-Register (zentral, `NFR-8`)

| Parameter | Wert | Ort | Status / Begründung |
|-----------|------|-----|---------------------|
| Szenenschwelle | 27.0 | `config.SCENE_THRESHOLD` | PySceneDetect-Default, unvalidiert |
| Energie-Gewichte | 0.40/0.35/0.15/0.10 | `_szene_energie` Fallback | heuristisch (Murch) |
| Beam-Score-Gewichte | 0.20/0.30/0.20/0.30 | `_beam_fill` | heuristisch |
| Bogen-Proportionen | 25/20/25 % | Bogen-Konstruktion | heuristisch |
| A-Roll-Bewegungsgrenze | 0.65 | Rollenklassifikation | heuristisch |
| Establishing-Grenzen | Lum 0.52 / Bew 0.38 / 3.5 s | Rollenklassifikation | heuristisch |
| Sprechpause | 300 ms | Subdivision | typische Satzpause |
| Lange-Szene-Grenze | 6 s | Post-Processing | heuristisch |
| Beam-Breite k | 3 | `_beam_fill` | Standard (Sutskever 2014) |

> **Verteidigungs-Hinweis:** Diese Tabelle ehrlich zeigen = wissenschaftliche Selbstkritik.
> Empfohlener nächster Schritt: Ablation Study + Nutzerstudie (`09_Abnahme §9.4`).
