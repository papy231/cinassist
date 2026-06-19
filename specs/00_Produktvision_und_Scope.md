# 00 — Produktvision & Scope

## 0.1 Vision

CinAssist ist ein **KI-gestütztes, nichtlineares Schnittsystem (NLE)**, das aus
unmontiertem Rohmaterial automatisch einen **kinematografischen Erzählbogen**
konstruiert. Das System soll dem menschlichen Editor nicht ersetzen, sondern als
**Entscheidungsunterstützungssystem** (*Decision Support System*) einen ersten,
nachvollziehbaren Schnittvorschlag liefern, den der Editor annimmt, anpasst oder verwirft.

> **Leitsatz (Soll):** „Das System nimmt unmontiertes Rohmaterial entgegen und
> *konstruiert* daraus eine Timeline mit narrativem Bogen — lokal, ohne dass Videodaten
> den Rechner verlassen."

## 0.2 Wissenschaftliche Fragestellung

> *Lassen sich drei Modalitäten — Bild (Vision), Ton (Audio) und Sprache (Language) — so
> kombinieren, dass ein System genug Inhaltsverständnis erlangt, um aus Rohmaterial
> automatisch einen kinematografischen Erzählbogen zu konstruieren?*

Diese Fragestellung definiert den Scope: CinAssist ist ein **multimodales** System. Jede
Anforderung in `01_Funktionale_Anforderungen.md` dient dieser Frage.

## 0.3 Ziele (Soll)

| ID | Ziel | Messbar an |
|----|------|------------|
| Z-1 | Vollautomatischer Schnittvorschlag aus ≥1 Rohclip | Timeline wird ohne manuellen Schnitt erzeugt |
| Z-2 | Nachvollziehbarkeit jeder Schnittentscheidung | Jedes Segment trägt Rolle + Begründung (keine Blackbox) |
| Z-3 | 100 % lokale Ausführung der Medienverarbeitung | Keine Pixel-/Audiodaten verlassen den Rechner (`NFR-1`) |
| Z-4 | Quantitative Selbstbewertung des Ergebnisses | Metriken `diversitaet`, `wechselrate`, `dialog_treue` (`AI-9`) |
| Z-5 | Lauffähig auf Apple Silicon ohne dedizierte GPU | mlx-whisper + CLIP CPU/MPS (`NFR-4`) |

## 0.4 Abgrenzung (Out of Scope)

Bewusst **nicht** Teil des Systems (begründet in `DEFENSE.md §2`):

- **Farbkorrektur / Color Grading** (Domäne von DaVinci Resolve).
- **Multi-Cam-Synchronisation** im professionellen Sinn (nur einfache Multicam-Dedup, `AI`).
- **Effekt-/Compositing-Pipeline**, Keyframe-Animation.
- **Cloud-Rendering / kollaborative Mehrbenutzer-Bearbeitung.**
- **End-to-End-Deep-Learning** des Schnitts (bewusst zugunsten von Interpretierbarkeit verworfen).

## 0.5 Abgrenzung gegenüber bestehenden NLEs

| Funktion | DaVinci / Premiere | CinAssist (Soll) |
|----------|:------------------:|:----------------:|
| Schnitterkennung in **fertigem** Material | ✓ | ✓ |
| Audiotranskription | ✓ (paid) | ✓ (Whisper, lokal) |
| Semantisches Szenenverständnis | ✗ | ✓ (CLIP + LLM) |
| A-Roll / B-Roll / Establishing Klassifikation | ✗ | ✓ |
| **Konstruktion** eines narrativen Bogens aus Rohmaterial | ✗ | ✓ (Freytag-Pyramide) |

→ Kernunterschied: NLEs **detektieren** Schnitte in montiertem Material; CinAssist
**komponiert** einen Schnitt aus Rohmaterial.

## 0.6 Anwendungsfälle (Use Cases)

- **UC-1 — Automatischer Schnitt:** Nutzer lädt 1–2 Rohclips hoch, wählt einen Stil, startet
  den KI-Schnitt und erhält eine fertige Timeline.
- **UC-2 — Prompt-gesteuerter Schnitt:** Nutzer gibt zusätzlich einen Text-Prompt an
  (z. B. „ruhige Außenaufnahmen, warmes Licht"); das System priorisiert passende Szenen.
- **UC-3 — Manuelle Nachbearbeitung:** Nutzer verschiebt/teilt Segmente im Timeline-Editor
  und lässt die Timeline per „Reorganize" neu ordnen.
- **UC-4 — Inspektion:** Nutzer öffnet den Pipeline-Bericht einer Szene, um Transkription,
  visuelle Metriken und Embedding-Status nachzuvollziehen (dient Z-2).
- **UC-5 — Export:** Nutzer exportiert die Timeline als MP4 mit Übergängen oder
  übergibt sie an ein externes NLE.

## 0.7 Glossar

| Begriff | Bedeutung |
|---------|-----------|
| **Clip** | Hochgeladene Rohvideodatei (Quelle „A" oder „B"). |
| **Szene** | Von PySceneDetect erkannter zusammenhängender Abschnitt eines Clips. |
| **Segment** | Element auf der Timeline; referenziert eine (Teil-)Szene. |
| **A-Roll** | Primäraufnahme mit Dialog/Sprecher (Interview). |
| **B-Roll** | Sekundäraufnahme ohne Dialog (Schnittbild, Aktion, Detail). |
| **Establishing Shot** | Einführende Einstellung (Ort, Atmosphäre). |
| **Ingestion** | Automatische Analyse-Pipeline nach dem Upload (Phase 2). |
| **Energie** | Skalarer Aufmerksamkeitswert einer Szene (0–1). |
| **Bogen / Arc** | Narrative Struktur nach Freytag (Ouverture → … → Climax → Cloture). |
| **Embedding** | 512-dim CLIP-Vektor zur Messung visueller Ähnlichkeit. |
