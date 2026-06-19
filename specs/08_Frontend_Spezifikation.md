# 08 — Frontend-Spezifikation

> Technologie: Next.js 16.2.3 (App Router), React 19, TypeScript 5, Tailwind CSS 4,
> Zustand 5, Framer Motion 12, Lucide React. Quelle: `src/`.

## 8.1 Seitenstruktur (App Router)

| Route | Datei | Zweck |
|-------|-------|-------|
| `/` | `src/app/page.tsx` | Dashboard / Startseite (Projektübersicht) |
| `/editor` | `src/app/editor/page.tsx` | Haupt-Editor (Timeline, Viewer, KI-Panel) |
| `/project/[id]` | `src/app/project/[id]/page.tsx` | Projekt-/Clip-Detailansicht |
| (Layout) | `src/app/layout.tsx` | Root-Layout, Schrift, Provider |

> **AGENTS.md beachten:** Diese Next.js-Version weicht von Trainingswissen ab. Vor
> Frontend-Änderungen MUSS die relevante Anleitung in `node_modules/next/dist/docs/`
> gelesen werden.

## 8.2 Komponenten (Soll-Verantwortung)

| Komponente | Datei | Aufgabe | Anforderung |
|------------|-------|---------|-------------|
| `AppSidebar` | `components/AppSidebar.tsx` | Seitennavigation | NFR-17 |
| `Dock` | `components/Dock.tsx` | Werkzeugleiste | — |
| `ProjectCard` | `components/ProjectCard.tsx` | Projektkarte im Dashboard | — |
| `TimelineEditor` | `components/Timeline/TimelineEditor.tsx` | Mehrspurige Timeline (Zoom, Drag&Drop, Split) | FR-27, FR-28 |
| `DualViewer` | `components/Viewer/DualViewer.tsx` | A/B-Vorschau nebeneinander (Proxys) | NFR-5b |
| `PipelineSteps` | `components/PipelineSteps.tsx` | Live-Anzeige der Ingestion-Schritte | FR-12 |
| `SceneDetail` | `components/SceneDetail.tsx` | Szenen-Inspektion (Transkription, Metriken, Embedding) | FR-13, Z-2 |
| `MaterialAtlas` | `components/MaterialAtlas.tsx` | 2D-Projektion der Embeddings | FR-31 |
| `MaterialRelations` | `components/MaterialRelations.tsx` | Multicam-/Beziehungsansicht | FR-32 |
| `ChatPanel` | `components/ChatPanel.tsx` | Chat-Assistent + Schnittvorschlag | FR-33 |

## 8.3 State-Management (Zustand)

Zwei fachlich getrennte Stores (`NFR-15`):

**`editorStore`** (`stores/editorStore.ts`) — globaler Editor-Zustand:
- Liste hochgeladener Clips (`ClipDTO[]`)
- aktive Jobs mit Fortschritt
- Backend-Verbindungsstatus
- Undo/Redo-Stack der Timeline (`FR-30`)

**`timelineStore`** (`stores/timelineStore.ts`) — Timeline-Zustand:
- platzierte Segmente (`TimelineSegment[]`)
- Playhead-Position (px)
- Zoom (px/Sekunde)
- ausgewählte Spur
- KI-Banner (Stil, Provider, Segmentanzahl, Metriken)

## 8.4 API-Anbindung
- Zentraler Client: `src/lib/api.ts` (Basis-URL `localhost:8001`).
- Job-Fortschritt: Hook `hooks/useJobStatus.ts` → `connectJobWs` (WebSocket).
- **FE-1:** Alle Backend-Aufrufe MÜSSEN über `api.ts` laufen (kein direktes `fetch` in
  Komponenten), damit Typen (DTOs) zentral bleiben.

## 8.5 Timeline-Editor — Interaktionen (Soll)

| ID | Anforderung |
|----|-------------|
| FE-2 | Zoom MUSS dynamisch sein (px pro Sekunde anpassbar). |
| FE-3 | Segmente MÜSSEN per Drag & Drop verschiebbar sein. |
| FE-4 | Ein Segment MUSS an der Klickposition teilbar sein (Split). |
| FE-5 | Segmentfarben: Orange = Clip A, Blau = Clip B, Grün = Audio/Music. |
| FE-6 | Vom KI-Schnitt erzeugte Segmente (`ai=true`) MÜSSEN visuell markiert sein. |
| FE-7 | „Reorganize" MUSS die aktuelle Timeline an `/api/ai/reorganize` senden und das Ergebnis anwenden. |

## 8.6 KI-Panel (Soll)

| ID | Anforderung |
|----|-------------|
| FE-8 | Das Panel MUSS die fünf LLM-Provider anzeigen; ein grüner Punkt markiert konfigurierte (`/api/ai/providers`). |
| FE-9 | Der Provider „Auto" MUSS automatisch den besten verfügbaren wählen. |
| FE-10 | Stil, Prompt, `qualitaet_schwelle`, `max_szenen` und LLM-Schalter MÜSSEN einstellbar sein. |
| FE-11 | Nach dem Schnitt MÜSSEN die Metriken (`diversitaet`, `wechselrate`, `dialog_treue`) sichtbar sein (Z-4). |

## 8.7 Fortschritt & Feedback (Soll)
- **FE-12:** Während Ingestion/Export MUSS ein Fortschrittsbalken aus den WebSocket-Daten
  angezeigt werden (`NFR-19`).
- **FE-13:** Der Backend-Verbindungsstatus (`/health`) MUSS sichtbar sein (`NFR-17`).

## 8.8 Designsystem
- CSS-Variablen (Farben, Typografie) in `src/app/globals.css`.
- Animationen über Framer Motion (Panels, Übergänge).
- Icons über Lucide React.
- **FE-14:** Layout SOLL der A/B-Logik folgen (zwei Quellen, Dual-Viewer).
