# 02 — Nichtfunktionale Anforderungen

> Qualitätsanforderungen an das System. Sie sind oft entscheidend für die wissenschaftliche
> Verteidigung, weil sie die Designentscheidungen begründen.

## 2.1 Datenschutz & Lokalität

| ID | Anforderung | Begründung |
|----|-------------|------------|
| NFR-1 | Die gesamte Medienverarbeitung (Pixel, Audio) MUSS lokal erfolgen. Keine Videodaten dürfen den Rechner verlassen. | Kernversprechen des Projekts; Datenschutz; Offline-Fähigkeit. |
| NFR-2 | Cloud-LLMs (Claude/GPT/Gemini) DÜRFEN ausschließlich **Beschreibungstext** verarbeiten, niemals Pixel oder Audio. | Begrenzt den Datenabfluss auf abgeleitete Metadaten. |
| NFR-3 | Das System MUSS ohne Internetverbindung lauffähig sein (Ollama + lokale Modelle als Default). | Reproduzierbarkeit, Unabhängigkeit von Drittanbietern. |

## 2.2 Plattform & Performance

| ID | Anforderung | Zielwert |
|----|-------------|----------|
| NFR-4 | Das System MUSS auf Apple Silicon (M-Serie) ohne dedizierte NVIDIA-GPU laufen. | mlx-whisper (MLX), CLIP über CPU/MPS. |
| NFR-5 | Die Ingestion eines 1–2-minütigen Clips SOLL in ca. 2–5 Minuten abgeschlossen sein. | Richtwert, hardwareabhängig. |
| NFR-5a | Lang laufende Aufgaben (Ingestion, Export) MÜSSEN außerhalb des HTTP-Threads laufen (Celery-Worker). | Verhindert Request-Timeouts; Tracking + Retry möglich. |
| NFR-5b | Das Frontend MUSS für die Video-Vorschau Proxys (≤960p) statt Originaldateien verwenden. | Browser-Performance, geringere Last. |

## 2.3 Reproduzierbarkeit & Wissenschaftlichkeit

| ID | Anforderung | Begründung |
|----|-------------|------------|
| NFR-6 | Der KI-Schnitt MUSS bei identischer Eingabe und deaktivierter LLM-Verfeinerung **deterministisch** sein. | Wissenschaftliche Wiederholbarkeit; LLM ist nicht-deterministisch und daher per Default aus. |
| NFR-7 | Jede Schnittentscheidung MUSS interpretierbar sein (Rolle, Energie, Begründung). Keine Blackbox. | Zentrale Designentscheidung gegen End-to-End-DL (siehe `DEFENSE.md §4`). |
| NFR-8 | Alle „Magic Numbers" (Schwellen, Gewichte) MÜSSEN an einer Stelle dokumentiert und benannt sein. | Erleichtert die Verteidigung; siehe `07_KI_Schnitt_Spezifikation.md §7.7`. |

## 2.4 Robustheit & Fehlerverhalten

| ID | Anforderung |
|----|-------------|
| NFR-9 | Schlägt ein Pipeline-Schritt fehl, MUSS der Job in den Status `fehler` mit Nachricht übergehen — ohne den Server zu beenden. |
| NFR-10 | Die LLM-Antwort-Auswertung MUSS robust gegenüber Reasoning-Text, JSON-Modus und unsauberem Format sein. |
| NFR-11 | Fehlt ein optionales Modell (z. B. LLaVA), MUSS das System auf einen Fallback ausweichen (z. B. LLaMA3), statt abzubrechen. |
| NFR-12 | Der Upload MUSS Format und maximale Dateigröße validieren. |

## 2.5 Wartbarkeit & Struktur

| ID | Anforderung |
|----|-------------|
| NFR-13 | Backend-Module MÜSSEN nach Verantwortung getrennt sein: `api/` (Endpunkte), `workers/` (Tasks), `core/` (Infrastruktur). |
| NFR-14 | Async- und Sync-DB-Zugriffe MÜSSEN getrennt sein (async für FastAPI, sync für Celery). |
| NFR-15 | Der Frontend-State SOLL in fachlich getrennten Stores liegen (Editor-State vs. Timeline-State). |
| NFR-16 | Konfiguration MUSS über Umgebungsvariablen erfolgen (keine Hardcodierung von Pfaden/Keys). |

## 2.6 Bedienbarkeit

| ID | Anforderung |
|----|-------------|
| NFR-17 | Der Verbindungsstatus zum Backend MUSS in der UI sichtbar sein. |
| NFR-18 | Verfügbare LLM-Provider MÜSSEN visuell als verfügbar/nicht verfügbar gekennzeichnet sein. |
| NFR-19 | Langlaufende Operationen MÜSSEN einen Fortschrittsbalken zeigen (kein „eingefrorenes" UI). |

---

### Bekannte Einschränkungen (ehrliche Limitationen)

Diese NFRs sind bewusst **nicht vollständig erfüllt**; sie gehören in das Limitations-Kapitel
der Arbeit (Details in `DEFENSE.md §5`):

- **NFR-6** gilt nur bei deaktiviertem LLM. Mit LLM ist der Schnitt nicht-deterministisch.
- **NFR-8** ist teilweise erfüllt: Phase-2-`energie` nutzt noch die heuristische
  Gewichtsformel; Phase-3-Scoring nutzt bereits CLIP-Zero-Shot.
- Es existiert **kein Ground-Truth-Datensatz** zur objektiven Evaluierung (siehe
  `09_Abnahme_und_Evaluierung.md`).
