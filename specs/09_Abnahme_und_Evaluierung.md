# 09 — Abnahme & Evaluierung

> Wann gilt eine Anforderung als erfüllt, und wie wird die Qualität des Schnitts gemessen?
> Dieses Dokument verbindet die Anforderungen mit überprüfbaren Kriterien.

## 9.1 Abnahme-Szenario (End-to-End, Soll)

Ein vollständiger Durchlauf MUSS ohne manuellen Eingriff funktionieren:

1. **Upload** zweier Clips A und B (je 30–120 s) → 2× `clip_id`, 2× `job_id` (`FR-1`).
2. **Ingestion** beider Clips → Status `analysiert`, ≥1 Szene je Clip mit Embedding,
   Transkription, `analyse_visuelle` (`FR-4`–`FR-9`).
3. **KI-Schnitt** `POST /api/ai/cut` → Timeline mit ≥3 Segmenten, jedem Segment eine Rolle,
   Metriken vorhanden, keine Post-Processing-Regel verletzt (`FR-14`–`FR-19`, `FR-24`).
4. **Bearbeitung**: ein Segment verschieben + Split (`FR-28`); „Reorganize" liefert neue
   Reihenfolge (`FR-29`).
5. **Export** `POST /api/export` → abspielbare MP4 in `outputs/` mit Übergängen (`FR-34`, `FR-35`).

**Abnahme bestanden**, wenn alle fünf Schritte ohne Server-Absturz und mit den genannten
Ergebnissen durchlaufen.

## 9.2 Funktionale Abnahmekriterien (Checkliste)

| Anforderung | Prüfung | OK? |
|-------------|---------|-----|
| FR-2 | Upload startet automatisch einen Ingestion-Job | ☐ |
| FR-11 | WebSocket liefert Fortschritt 0→100 | ☐ |
| FR-14 | `/api/ai/cut` liefert HTTP 200 + Timeline | ☐ |
| FR-16 | Jedes Segment trägt eine Rolle | ☐ |
| FR-18 | Reihenfolge entsteht per Beam Search (nicht reine Energie-Sortierung) | ☐ |
| FR-19 | Keine 3 gleichen Quellclips / 3 langen Szenen in Folge | ☐ |
| FR-24 | Metriken im Response vorhanden | ☐ |
| FR-34 | Export erzeugt abspielbare MP4 | ☐ |
| FR-39 | Funktioniert ohne Cloud-API-Key (Ollama) | ☐ |
| NFR-6 | Gleiche Eingabe + LLM aus → identische Timeline | ☐ |

## 9.3 Nichtfunktionale Prüfungen

| Anforderung | Prüfmethode |
|-------------|-------------|
| NFR-1 / NFR-2 | Netzwerk-Monitoring während Ingestion: kein Upload von Pixel-/Audiodaten. Bei aktiviertem Cloud-LLM nur Textbytes. |
| NFR-4 | Lauf auf Apple Silicon ohne NVIDIA-GPU dokumentieren. |
| NFR-5 | Ingestion-Zeit eines 1–2-min-Clips messen (Zielbereich 2–5 min). |
| NFR-6 | KI-Schnitt 3× mit identischer Eingabe → identische Segmentliste (LLM aus). |
| NFR-9 | Absichtlicher Fehler (defekte Datei) → Job-Status `fehler`, Server lebt weiter. |

## 9.4 Evaluierung der Schnittqualität

### 9.4.1 Automatische Metriken (vorhanden, `AI-9`)
`diversitaet`, `wechselrate`, `dialog_treue`, `prompt_relevance`. Diese sind
**selbstberechnet** und ersetzen keine externe Validierung — aber sie machen Qualität
quantifizierbar und vergleichbar zwischen Stilen/Parametern.

### 9.4.2 Geplante Evaluierung (Limitation → Ausblick der Arbeit)

| Methode | Beschreibung | Status |
|---------|--------------|--------|
| **Ablation Study** | Jede Pipeline-Komponente einzeln deaktivieren (Bogen aus / Beam→Greedy / kein CLIP) und Metriken vergleichen. | geplant |
| **Nutzerstudie** | ≥20 Probanden, 5er-Likert-Skala (Kohärenz, Rhythmus, narrative Klarheit). | geplant |
| **Editor-Vergleich** | Qualitativer A/B-Test gegen einen menschlichen Schnitt. | geplant |
| **Parameter-Sweep** | Energie-/Beam-Gewichte variieren, Wirkung auf Metriken messen. | geplant |

> **Ehrlich benennen:** Es gibt **kein Ground-Truth-Dataset** für „guten" Schnitt. Das ist
> die größte methodische Limitation und gehört explizit ins Limitations-/Ausblick-Kapitel
> (`DEFENSE.md §5.4`).

## 9.5 Soll/Ist-Lücken (offen)

| Thema | Soll | Ist | Maßnahme |
|-------|------|-----|----------|
| `librosa` | reproduzierbares Setup | im Code genutzt, fehlt in `requirements.txt` | Dependency nachtragen (`NFR-3`) |
| `ai_old.py` | sauberer Code | Altdatei mit doppeltem `/cut` | entfernen/deprecaten |
| Phase-2-Energie | konsistentes Scoring | nutzt Magic-Number-Formel | als Fallback dokumentieren (`AI-1`) |
| Determinismus | immer reproduzierbar | nur mit LLM aus | im UI klar kennzeichnen |
| Evaluierung | externe Validierung | nur Selbstmetriken | Ablation + Nutzerstudie planen |

Diese Tabelle ist die ehrliche Brücke zwischen „was spezifiziert ist" und „was der Code
heute tut" — genau das, was ein Code-Betreuer sehen will.
