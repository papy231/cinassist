# 01 — Funktionale Anforderungen

> Verbindliche Anforderungen an das Verhalten des Systems. Status-Marker zeigen den
> aktuellen Umsetzungsgrad. MUSS/SOLL/KANN nach RFC 2119.

## 1.1 Import & Ingestion

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-1 | Das System MUSS Videodateien (MP4/MOV) per Upload entgegennehmen und als Quelle „A" oder „B" markieren. | MUSS | implementiert |
| FR-2 | Nach jedem Upload MUSS automatisch eine asynchrone Ingestion-Pipeline starten (nicht-blockierend). | MUSS | implementiert |
| FR-3 | Das System MUSS pro Clip technische Metadaten ermitteln: Dauer, Auflösung, FPS, Codec, Dateigröße. | MUSS | implementiert |
| FR-4 | Das System MUSS die Audiospur transkribieren (Whisper) inkl. Wort-Zeitstempel. | MUSS | implementiert |
| FR-5 | Das System MUSS Szenengrenzen automatisch erkennen (PySceneDetect). | MUSS | implementiert |
| FR-6 | Pro Szene MUSS eine visuelle Analyse berechnet werden: Luminanz, Farbtemperatur, Kontrast, Bewegung, Schärfe, Energie. | MUSS | implementiert |
| FR-7 | Pro Szene MUSS ein 512-dim CLIP-Embedding berechnet und gespeichert werden. | MUSS | implementiert |
| FR-8 | Pro Szene SOLL eine textuelle Beschreibung generiert werden (Vision-/Text-LLM). | SOLL | implementiert |
| FR-9 | Das System MUSS für jede Szene ein Thumbnail erzeugen. | MUSS | implementiert |
| FR-10 | Das System SOLL Browser-Proxys erzeugen: niedrig aufgelöstes Proxy-Video, Waveform-PNG, Thumbnail-Strip. | SOLL | implementiert |

**Abnahmekriterium (FR-1…FR-9):** Nach Upload eines 30–120 s Clips liegt nach Abschluss
des Jobs in der DB ein Clip mit Status `analysiert` und ≥1 Szene mit nicht-leerem
Embedding, Transkription und `analyse_visuelle` vor.

## 1.2 Fortschritt & Transparenz

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-11 | Das System MUSS den Fortschritt jedes Jobs (0–100 %) in Echtzeit per WebSocket übertragen. | MUSS | implementiert |
| FR-12 | Jeder Pipeline-Schritt MUSS einzeln meldbar sein (Schritt-Name + Schritt-Daten). | SOLL | implementiert |
| FR-13 | Das System MUSS pro Clip einen Pipeline-Bericht bereitstellen (Schritt-Historie + Szenen-Detail inkl. Embedding-Norm). | SOLL | implementiert |

**Abnahmekriterium:** Während einer laufenden Ingestion empfängt das Frontend
mindestens je einen WebSocket-Frame mit `progress` zwischen 0 und 100 und sieht den
abschließenden Status `fertig`.

## 1.3 KI-Schnitt (Kernfunktion)

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-14 | Das System MUSS aus den analysierten Szenen automatisch eine geordnete Timeline erzeugen (`POST /api/ai/cut`). | MUSS | implementiert |
| FR-15 | Der Schnitt MUSS einen wählbaren **Stil** unterstützen (z. B. kinematisch, dokumentarisch, Werbespot, Kurzfilm, Social Media). | MUSS | implementiert |
| FR-16 | Das System MUSS jede Szene einer **narrativen Rolle** zuordnen (A-Roll / B-Roll / Establishing). | MUSS | implementiert |
| FR-17 | Das System MUSS einen **kinematografischen Bogen** (Ouverture → Action → Transition → Climax → Cloture) aufbauen. | MUSS | implementiert |
| FR-18 | Die Szenenreihenfolge MUSS per **Beam Search** (Breite 3) optimiert werden, nicht greedy. | MUSS | implementiert |
| FR-19 | Das System MUSS Nachbearbeitungsregeln anwenden: kein dreifacher Clip-Wiederholung, keine 3 langen Szenen in Folge, A/B-Alternierung. | MUSS | implementiert |
| FR-20 | Lange Szenen MÜSSEN audio-bewusst geteilt werden (Schnitt an Sprechpausen > 300 ms). | SOLL | implementiert |
| FR-21 | Das System SOLL eine optionale LLM-Verfeinerung der Reihenfolge anbieten (Multi-Provider). LLM-Verfeinerung ist standardmäßig **deaktiviert** (Reproduzierbarkeit, `NFR-6`). | KANN | implementiert |
| FR-22 | Das System SOLL einen **Text-Prompt** unterstützen, der Szenen nach inhaltlicher Relevanz priorisiert (CLIP-Text-Encoder + MMR). | SOLL | implementiert |
| FR-23 | Das System KANN **Beat-Sync** unterstützen (Schnittpunkte an Musik-Beats, librosa). | KANN | teilweise |
| FR-24 | Das System MUSS nach dem Schnitt **Qualitätsmetriken** zurückgeben (Diversität, Wechselrate, Dialog-Treue, ggf. Prompt-Relevanz). | MUSS | implementiert |
| FR-25 | Das System MUSS die verwendete Scoring-Methode ausweisen (CLIP-Zero-Shot vs. heuristische Formel). | SOLL | implementiert |

**Abnahmekriterium (FR-14…FR-19):** `POST /api/ai/cut` mit einem analysierten Clip liefert
HTTP 200, eine gespeicherte Timeline mit ≥3 Segmenten, jedem Segment ist eine Rolle
zugeordnet, und keine der Post-Processing-Regeln aus FR-19 wird im Ergebnis verletzt.

## 1.4 Timeline-Bearbeitung

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-26 | Das System MUSS Timelines speichern, laden, aktualisieren und löschen (CRUD). | MUSS | implementiert |
| FR-27 | Der Editor MUSS mehrspurige Darstellung mit Zoom (px/Sekunde) bieten. | MUSS | implementiert |
| FR-28 | Der Nutzer MUSS Segmente per Drag & Drop verschieben und teilen (Split) können. | MUSS | implementiert |
| FR-29 | Das System SOLL eine bestehende, manuell veränderte Timeline neu ordnen können (`POST /api/ai/reorganize`). | SOLL | implementiert |
| FR-30 | Das System SOLL Undo/Redo der Timeline-Änderungen unterstützen. | SOLL | implementiert |

## 1.5 Analyse-Werkzeuge

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-31 | Das System SOLL einen **Material-Atlas** liefern: 2D-Projektion (PCA) der Szenen-Embeddings (`POST /api/ai/atlas`). | KANN | implementiert |
| FR-32 | Das System SOLL **Multicam-/Duplikat-Erkennung** anbieten (`POST /api/ai/multicam`). | KANN | implementiert |
| FR-33 | Das System KANN einen **Chat-Assistenten** über das Projektmaterial anbieten (`POST /api/ai/chat`). | KANN | implementiert |

## 1.6 Export

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-34 | Das System MUSS die Timeline als MP4 (H.264/AAC) exportieren (asynchroner Job). | MUSS | implementiert |
| FR-35 | Der Export MUSS Übergänge anwenden: Dissolve, Fade, FadeBlack, WipeLeft. | MUSS | implementiert |
| FR-36 | Das System SOLL die Timeline an ein externes NLE übergeben können (`POST /api/export/open-in`, z. B. DaVinci-Import). | KANN | implementiert |
| FR-37 | Eine Auflösung MUSS wählbar sein (Standard 1920×1080). | MUSS | implementiert |

**Abnahmekriterium (FR-34/35):** `POST /api/export` startet einen Job; nach Abschluss
liegt unter `outputs/` eine abspielbare MP4-Datei mit den definierten Übergängen vor.

## 1.7 Provider-Verwaltung

| ID | Anforderung | Prio | Status |
|----|-------------|------|--------|
| FR-38 | Das System MUSS die verfügbaren LLM-Provider und deren Modelle anzeigen (`GET /api/ai/providers`). | MUSS | implementiert |
| FR-39 | Das System MUSS ohne Cloud-API-Key vollständig funktionieren (Ollama lokal als Fallback). | MUSS | implementiert |

---

### Rückverfolgbarkeit (Traceability)

| Anforderung | Code-Verweis |
|-------------|--------------|
| FR-1…FR-3 | `backend/api/clips.py` (`/upload`) |
| FR-4…FR-10 | `backend/workers/ingest.py` |
| FR-11…FR-13 | `backend/api/websocket.py`, `backend/api/clips.py` (`/pipeline`) |
| FR-14…FR-25 | `backend/api/ai.py` (`/cut`) |
| FR-26 | `backend/api/timelines.py` |
| FR-29, FR-31, FR-32 | `backend/api/ai.py` (`/reorganize`, `/atlas`, `/multicam`) |
| FR-33 | `backend/api/chat.py` |
| FR-34…FR-37 | `backend/workers/export.py`, `backend/api/export.py` |
| FR-38, FR-39 | `backend/api/ai.py` (`/providers`) |
