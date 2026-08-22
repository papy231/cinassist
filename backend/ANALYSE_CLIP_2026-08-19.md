# Clip-Analyse (Ebene 2) — Halluzinationen eindämmen · Stand 2026-08-19

Betrifft die Analyse **pro Clip** (Modal „Analyse“: Übersicht / Szenen / Transkription / Bericht),
nicht die Sync-Ebene (Take-Modell, s. `core/sync/README.md`).

## Befund (vor der Änderung, Korpus Pinky Promise)

| Symptom | Ursache |
|---|---|
| Szenenbeschreibung nennt Personen/Geräte, die es nicht gibt („another person partially visible“, „remote controls … watching television“), Gefühle („enjoying her time“) | moondream auf **einem 320-px-Thumbnail** pro Szene; ein Take von 200 s = 1 Frame; Modell frei formulierend |
| „wide_no_person“ / 0 Gesichter bei zwei sichtbaren Personen | Haar-Frontal-Kaskade auf 320 px in Totalen/Profil chancenlos |
| Doppelte Szenen (2× Szene 1) nach Retry/Neu-Analyse | Persistenz hat alte `szenen`-Zeilen nicht gelöscht |
| Bericht: Genre „Vlog“, „genießt ihren Tee“, erfundene Zuordnungen | llama3, Temperatur 0,15, kein Projekt-Kontext, keine Belegpflicht, keine Nachprüfung |
| Keine Sprecher (Diarization „indisponible“) | pyannote 4.x liefert `DiarizeOutput` statt `Annotation` (`.itertracks` fehlt) |

## Maßnahmen

1. **Stichproben statt Einzelframe** (`workers/ingest.py::schritt_bildanalyse`): **adaptiv** — ein Frame
   alle `BILD_INTERVALL_S` = 30 s (1–12 je Szene, gleichmäßig, nie am Rand), **896 px aus dem Proxy**.
   Vor dem Bildmodell wird jeder Frame per **CLIP** mit dem vorigen verglichen: Kosinus ≥ 0,93 = „gleiches
   Bild“ → Beschreibung/Zählung werden übernommen (`gleich_wie`), kein Modellaufruf. Ein 4-min-Take ergibt
   so 8 Frames, davon typischerweise 3–5 tatsächlich beschrieben (~30–50 s). Warum nicht 3 feste Punkte:
   auf langen Takes ist das nicht repräsentativ; warum nicht jede Sekunde: Kosten ohne Informationsgewinn. Jeder Frame → Beschreibung +
   Personenzahl + Gesichter. Aggregation: Beschreibung = Mittel-Frame, `personen` = Median, Framing aus
   Gesichtsgröße; ohne Gesicht aber Personen ≥ 1 → „Totale mit Person(en)“. Alles landet in
   `szenen.analyse_visuelle.stichproben/personen` (keine neue Spalte) und wird im Szenen-Tab gezeigt.
2. **Faktisches Bildmodell** (`core/vision_describe.py`): primär **llava:7b** mit engem Prompt („only what
   is clearly visible … do not guess feelings“), Fallback moondream. Messung: llava zählt Personen korrekt
   („2“), moondream erfand bei 320 px zwei zusätzliche Personen. Zusätzlich **deterministisches
   „Entspekulieren“**: Nebensätze mit *as if / possibly / suggesting / appears to be enjoying / likely /
   cozy …* werden abgeschnitten — nie ergänzt (Tests `tests/test_bildanalyse.py`).
3. **Gesichter**: Haar frontal **+ Profil (+ gespiegelt)**, `minSize` relativ zur Bildbreite, NMS; auf
   896-px-Frames. Bleibt eine Heuristik → deshalb die llava-Zählung als zweite Quelle.
4. **Keine Duplikate**: vor dem Speichern werden `szenen`/`speakers`/`scene_speakers` des Clips gelöscht.
   Neuer Endpunkt `POST /api/clips/{id}/neu-analysieren` + Kontextmenü „Neu analysieren (komplett)“.
5. **Diarization repariert** (`core/diarize.py`: `result.speaker_diarization`), und die Sprecher werden an
   die Whisper-Segmente geheftet (`transkription_json[*].sprecher`, größte Überlappung) → Transkript-Tab
   zeigt „Sprecher A/B“, der Bericht bekommt „wer sagt was“.
6. **Bericht (Synthese)** (`api/clips.py::clip_synthese`):
   - Modell **qwen2.5:14b** (falls installiert; sonst `OLLAMA_MODEL`), Temperatur 0, `num_ctx` 8192.
   - Prompt: nur Belege (Transkript mit Zeit + Sprecher, Stichproben-Beschreibungen mit Zeit, Framing,
     Personenzahl, Take-Metadaten Szene/Einstellung/Take, Tonquelle), **Projekt-Kontext** aus den
     Einstellungen als Fakt (Genre wird nicht geraten → „unbekannt“ ohne Kontext), Felder `belege` und
     `unsicher` verpflichtend, Regeln „Anrede ≠ Sprecher“, keine Zustands-/Absichtszuschreibung.
   - **Deterministische Nachprüfung**: genannte Personen müssen im Glossar/Kontext + Transkript belegt
     sein (sonst entfernt + Hinweis); Anrede-Regel (Glossar-Name als Anrede im Dialog → anwesend);
     Genre ohne Kontext → „unbekannt“; `unsicher` wird um harte Fakten ergänzt (kein Dialog, keine
     Diarization, kein Kontext). UI zeigt Belege + „Nicht belegt / Nachprüfung“ + Grundlage-Zähler.
7. **Einstellungen → Projekt-Kontext + Max. Sprecher** (`/api/system/projekt`, `DATA_DIR/einstellungen.json`).
   `max_sprecher` deckelt die pyannote-Diarization (beobachtet: 4–5 „Sprecher“ für eine schreiende Stimme).
8. **Robustheit im Lauf**: NaN/Inf aus Whisper (large-v3) → `None` vor dem JSON-Insert (`_json_sauber`);
   Takes mit **mehreren verlinkten WAVs** nehmen jetzt das Audio mit der größten **Abdeckung des Videos**
   (nicht die höchste Konfidenz — ein 1,6-s-Fehlstart hatte 0,99 und gewann gegen die 154-s-Aufnahme).
9. **Whisper-Modell** auf `qualitaet` (large-v3) gestellt: Korpus-Transkripte sind sichtbar sauberer
   („Yuri“ durchgehend, weniger Verhaspler); ~1,5–2× langsamer als turbo.

## Was bleibt (ehrlich)

- Ein VLM bleibt ein VLM: llava hielt in einem Frame einen Pelzmantel für „a dog“. Stichproben + Zählung +
  Belegpflicht senken die Rate, eliminieren sie nicht. Der Bericht kennzeichnet, was nicht belegt ist.
- Kosename vs. Name („Babe“ = Yuri?) kann kein Modell aus dem Glossar wissen — der Projekt-Kontext ist
  dafür da („Babe = Kosename für Yuri“).
- Kosten: Bildanalyse ~6 s pro Frame (llava) → ~20 s pro Take mit 3 Frames; Bericht ~30–50 s (qwen 14b,
  on-demand, gecacht).

## Bedienung

- Einstellungen → **Projekt-Kontext** (Projektart, Titel, Figuren, Kosenamen) und **Transkription →
  Glossar** pflegen.
- Clip: Rechtsklick → **Neu analysieren (komplett)** (Bild + Ton) oder **Neu transkribieren** (nur Ton).
- Analyse-Modal → **Bericht → Neu generieren** nach Änderung des Kontexts.
