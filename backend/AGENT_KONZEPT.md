# KI-Agent — Konzept & Stand (2026-08-20)

Ziel (Nutzer-Auftrag): ein Agent, der **die Intention wirklich versteht und wirklich handelt** — verbunden mit allem,
was CinAssist über das Projekt weiß (Drehbuch, Beats, Schnittplan), und mit Händen an der Timeline.

## Architektur

```
ChatPanel (SSE, Historie 8 Turns) ──► /api/agent/chat/stream
                                          │  ReAct-Schleife (qwen2.5:14b, JSON, num_ctx 16k)
                                          │  System-Prompt = Projekt-Kontext + Timeline-Snapshot (tlIds!) + Stil-Präferenzen
                                          ▼
                       Wissen                              Handeln (IMMER Vorschlag, nie Direkt-Anwendung)
   get_script_overview · get_scene_context      edit_timeline (TimelineCmds, server-validiert)
   get_take_details · get_plan (grund+beleg)    regenerate_schnittplan (echter L5-Beat-Planer)
   search_transcripts                           swap_beat_source (Beat-Matrix)
                                          │
                                          ▼
      traceToProposals (agent-trace.ts) ──► ProposalStore ──► Geister-Vorschau im Editor ──► Nutzer akzeptiert/verwirft
```

- **Code:** `backend/api/agent_kontext_tools.py` (8 neue Tools + `pruefe_timeline_kommandos`),
  `backend/api/agent.py` (Registrierung, Projekt-Block, Routing, Schleifen-Wächter),
  `src/lib/agent-trace.ts` (edit_timeline-/Generator-Proposals), `src/components/ChatPanel.tsx` (Historie).
- **Alt-Tools** (Silences, Speaker, CLIP-Suche, alte Generatoren) bleiben unverändert daneben.

## Entscheidungen

1. **Vorschlag statt Direkt-Anwendung.** Jedes Handeln-Tool endet in einem Proposal mit Geister-Vorschau
   (bestehender ProposalStore); der Nutzer akzeptiert im Editor. Der Agent wird im Prompt verpflichtet, das zu sagen.
2. **Server-Validierung der Kommandos** (`pruefe_timeline_kommandos`, pur, 13 pytest-Fälle): unbekannte tlIds → Fehler;
   Zeiten auf [0, Gesamtdauer] geklemmt; Trim darf einen Clip nie auslöschen (≥ 0,5 s bleiben); Fades ≤ 10 s,
   Gain −60…+12 dB; unbekannte Typen fallen einzeln raus statt alles zu blockieren. Warnungen/Fehler gehen an
   LLM UND Nutzer (Regel: final_answer muss sie nennen).
3. **Projekt-Bewusstsein per Default:** jeder Lauf lädt kompakt Drehbuch/Szenen/Figuren-Mapping/aktuellen Plan in den
   System-Prompt; Timeline-Snapshot-Zeilen enthalten jetzt die **tlId** (nötig für trim/delete/move).
4. **Routing-Heuristik + Regeln:** Frage-Muster → „Auskunfts-Modus“ (nur Wissens-Tools), Editier-Verben →
   „Bearbeitungs-Modus“ (muss in einem Handeln-Tool enden). Regel 8: WARUM-Fragen → get_plan, grund **wörtlich
   zitieren** (Anti-Halluzination — beobachtet: „beste Bildqualität“ erfunden, seitdem Zitierpflicht).
5. **Schleifen-Wächter:** identischer Tool-Call (Name+Argumente) zum 3. Mal → erzwungene Synthese
   (beobachtet: 12× get_plan, weil die Observation auf 1200 Zeichen gekürzt war und die relevante Zeile fehlte →
   Kürzung auf 4000 angehoben + get_plan hat einen `szene`-Filter).
6. **Observation-Kompaktierung:** große Felder (segments, eintraege, transkript, beat_spans, commands) gehen NICHT roh
   ins LLM-Kontextfenster, sondern als kompakte Zeilen (`Nr10 Sz2 2.1 T4 82–103s B[3] — <grund>`); das Frontend
   bekommt weiterhin die volle Observation (für Proposals).
7. **Historie:** ChatPanel schickt die letzten 8 user/assistant-Turns; „Mach lieber 3 Sekunden“ funktioniert.

## Getestete Szenarien (run_sync gegen echte DB + Ollama — Skript: scratchpad/agent_szenarien.py)

| # | Prompt | erwartet | Ergebnis |
|---|--------|----------|----------|
| S1 | „Warum wurde in Szene 2 für Beat 3 die Einstellung 2.1 Take 4 gewählt?“ | get_plan, Zitat des grund | ✅ zitiert wörtlich „Anker, Bild-Beleg, nicht der beste Take seiner Einstellung“ |
| S2 | „Quelles lignes manquent dans le plan actuel ?“ | Wissens-Tool, Lücken-Liste | ✅ alle 4 Lücken mit Grund |
| S3 | „Wo wird ‚Tee machen‘ gesagt?“ | search_transcripts | ✅ 3 Takes mit Zeitstempeln |
| S4 | „Kürze den ausgewählten Clip um 2 s am Ende“ (+Snapshot) | edit_timeline trim tlId des SELECTED, −2 | ✅ Kommando exakt |
| S5 | „Mach lieber 3 Sekunden.“ (+Historie von S4) | trim −3 auf denselben Clip | ✅ |
| S6 | „Zeige in Szene 2 den Beat 3 aus 2.1 Take 2 statt Take 4“ | swap_beat_source, Segmente | ✅ |
| S7 | „Erzeuge einen neuen Feinschnitt.“ | regenerate_schnittplan, Plan + Zahlen | ✅ 31 Segmente, 445,5 s, 4 Lücken |

Unit-Tests: `backend/tests/test_agent_kommandos.py` (13 Fälle, grün; Voll-Suite backend 81 passed / 15 skipped).
Browser: ChatPanel → Vorschlag mit Geister-Vorschau (edit_timeline) im Editor verifiziert.

## Sprache

**Die gesamte Agent-Konversation ist DEUTSCH** (Nutzer-Vorgabe 20.08.) — egal in welcher Sprache gefragt wird
(getestet: frz. Frage → deutsche Antwort, S10). Nur eine explizite Stil-Präferenz `language` (fr/en) übersteuert das;
dann greift die Übersetzungs-Passe auf die finale Antwort. Die frühere Auto-Erkennung der Fragesprache ist entfernt.

## Interpretation vor Aktion (Regel 12)

Redaktionelle Aufträge sind implizit — der Agent interpretiert sie wie ein Profi-Cutter und NENNT seine Annahmen:
(a) Klappe/Slate wird nie mitgeschnitten (Spielfenster), (b) bei mehreren Takes derselben Szene zählt der beste,
(c) „chronologisch“ = Skript-Reihenfolge, (d) kein Moment doppelt. Ist ein Auftrag mehrdeutig mit wesentlich
verschiedenen Ergebnissen, stellt er EINE Rückfrage mit Optionen statt zu raten. Dafür gibt es das neue Handeln-Tool
**`lege_sequenzen_chronologisch`** (je Szene ein Segment, bester Take per `take_score`, getrimmt via `_spiel_grenzen`
= Klappe/Einrichten raus, Skript-Reihenfolge, optional `max_s_pro_szene`) — die „Sichtungs-Fassung“, bewusst getrennt
vom Beat-Feinschnitt (`regenerate_schnittplan`). Getestet: S9 — „Lege die Sequenzen chronologisch nach dem Skript auf
die Timeline“ → richtiges Tool, alle Segmente überspringen die Klappe, final_answer listet Annahmen + Take-Wahl je Szene.

## Performance-Pass (2026-08-20, Nutzer: „plus rapide et plus précis, dois-on upgrade?“)

Drei Stufen, jede gegen die Szenario-Batterie gemessen (`backend/tests/szenarien_agent_live.py`, jetzt mit
Timing + `--json`-Dump; Bench-Dateien im Scratchpad):

1. **Gratis-Gewinne:** `keep_alive 30m` (Modell blieb vorher NICHT geladen → jede erste Anfrage zahlte den
   9-GB-Load) · **dynamisches Tool-Set** nach Intention (Frage → 8 Wissens-Tools, Bearbeitung → Wissens+Handeln;
   Prompt 13,7k → 5,2k Zeichen) · **stabiler Prompt-Präfix** (Volatiles — Snapshot/Playhead/Stil — ans Ende →
   Ollama-KV-Cache greift über Schritte und Nachrichten) · **deterministischer Fast-Path** für zwei Frage-Muster
   („Warum Szene N Beat M?“ → get_plan + wörtliches Zitat; „Wo wird ‚X‘ gesagt?“ → search_transcripts) — 0 LLM-Calls,
   exakte Antwort mit Belegen.
2. **Natives Tool-Calling** (`/api/chat` + `tools`, Flag `CINASSIST_AGENT_NATIVE=0` für die alte JSON-Schleife):
   präzisere Argumente, 1 Call weniger pro Lauf, Antworten direkt in der Nutzersprache. Neuer Befund dabei:
   das Modell erfindet `plan_id`-Werte („aktuel“) → `_letzter_plan` fällt bei ungültiger UUID jetzt auf den
   neuesten Plan zurück statt „kein Plan“ zu melden.
3. **Modell-Benchmark:** qwen3:30b-a3b passte nicht auf die Platte (12 GB frei / ~19 GB nötig); **qwen3:8b**
   getestet und **verworfen**: schneller (S4 18,6 s), aber unpräzise — S2 behauptete „keine Lücken“ (falsch),
   S5 fabrizierte einen Vorschlag OHNE das Tool aufzurufen. → **qwen2.5:14b bleibt** (per `CINASSIST_AGENT_MODEL`
   umschaltbar; qwen3-`think` wird automatisch abgeschaltet, falls doch mal ein qwen3 gesetzt wird).

| Szenario | Baseline (JSON, kalt) | Stufe 1 | Stufe 2 (nativ) |
|---|---|---|---|
| S1 Warum-Frage | 88,2 s | **0,1 s** (Fast-Path) | 0,1 s |
| S3 Transkript-Suche | 30,7 s | **0,0 s** (Fast-Path) | 0,1 s |
| S4 Clip kürzen | 82,0 s | 49,2 s | **32–35 s** |
| S5 Folgeauftrag | 89,3 s | 89,3 s | **68–73 s** |
| S2 Lücken (frz.) | 67,0 s | 67,0 s | 56–92 s (inkl. Kalt-Load) + antwortet jetzt auf Französisch |
| S6 Beat-Tausch | 29,3 s | 29,3 s | 68–77 s (⚠ langsamer im Chat-Format — Varianz/Template, beobachten) |

Alle 6 Szenarien fachlich korrekt (Asserts grün), pytest-Suite grün.

## Live-Befund 20.08. (erster echter Nutzer-Lauf) + Fixes

Der Nutzer-Lauf „Lege die Sequenzen chronologisch …“ (3,8 min, Klappe sichtbar, Szene 5 unvollständig) deckte vier Dinge auf:
1. **„Lege/Platziere/Erstelle/Baue“ fehlten in der Editier-Verben-Heuristik** → der Lauf lief mit dem VOLLEN Tool-Set
   (13,7k-Prompt). Verben ergänzt → gleicher Auftrag jetzt ~77 s.
2. **Sichtbare Klappe:** `_spiel_grenzen` schneidet nur die GESPROCHENE Klappe; das Zuklappen der Tafel kommt später
   (und stumme Takes haben keine gesprochene). → Tool nutzt zusätzlich `anfang_nach_klappe` (Bewegungs-Spike) +
   `ende_bereinigen` (Ausstieg, gedeckelt auf 15 s — sonst hätte es 38 s Finale der Szene 2 gekostet) + 0,5-s-Boden.
3. **Szenen-TEILE:** „ein Segment je Szene“ verschluckte Teil 5.2 → bei dreistufiger Klappe jetzt ein Segment je Teil
   (bester Take je Teil, Teil-Reihenfolge); Szenen explizit nach `reihenfolge` sortiert.
4. **Live-Anzeige:** während des Laufs zeigt der Chat jetzt deutsche Labels („Chronologische Fassung bauen…“) und
   1-Zeilen-Ergebnisse („Ergebnis: 6 Segmente · 523 s“) statt roher JSON-Args/Observations; die Rohdaten bleiben nach
   Abschluss hinter „Schritte anzeigen“. Außerdem: halluziniertes `max_s_pro_szene: 0` wird ignoriert (kein 0-s-Cap).

## Vorschau-Workflow (20.08., Nutzer-Wunsch)

Jede Proposal-Karte hat jetzt drei Wege: **Vorschau** legt den Vorschlag PROBEWEISE auf die Timeline (voll abspielbar,
nichts gespeichert; Status `previewing`, nur eine Vorschau gleichzeitig — eine zweite stellt die erste zurück) ·
**Annehmen** schreibt fest — und speichert bei Sequenz-Vorschlägen (loadSequence replace) die Fassung automatisch im
Backend (`stil=rohschnitt`; kleine Edits wie Trims bleiben wie bisher im Speicher bis „Speichern“) · **Verwerfen**
stellt den Zustand VOR der Vorschau exakt wieder her. Mechanik: `executor.captureState()/restoreState()` (Editor) +
`previewProposal` im ProposalStore + optionales `executor.persist(label)`. Browser-verifiziert: 31 Clips → Vorschau
2 Clips → Verwerfen 31 Clips → Vorschau+Annehmen → Timeline-Zeile im Backend. Grenze: Marker/In-Out-Änderungen einer
Proposal werden beim Verwerfen nicht zurückgestellt (nur Clips) — Agent-Proposals enthalten die praktisch nie.

## Sichtungs-Fassung v3 (20.08., Nutzer-Korrektur: „Dedupe-Einheit ist der TAKE, nicht die Szene“)

„Ein Segment je Szene“ war falsch — es fehlten ganze Einstellungen (2.2/2.3/2.4, 4.1/4.3/4.4 …). Richtig:
Takes DERSELBEN Einstellung sind Wiederholungen derselben Aufführung (T1/T2 → der beste zählt), verschiedene
EINSTELLUNGEN sind eigene Blickwinkel/Inhalte und gehören alle in die Sichtung. `lege_sequenzen_chronologisch` legt
jetzt **ein Segment je Einstellung** (bester Take, Reihenfolge Szene → Teil → Einstellung) und bei Szenen OHNE Dialog
(Insert-Ordner, alle „1.1“) **ein Segment je Motiv** (CLIP-Bild-Cluster ≥ 0,90 — 13 Takes → 11 Motive, gekappt auf 5 s).
Ergebnis am Korpus: 27 Segmente / ~19 min statt 6 Segmente / 9 min. Zwei Fenster-Bugs dabei gefixt: Produktions-Sprech
mitten im Take („Hallo?“ Richtung Crew) schob den Einstieg hinter das Spiel (4.3 T1 → 1-s-Segment) und der visuelle
Klappen-Skip konnte die erste Replik halb verschlucken — der Einstieg liegt jetzt NIE hinter der ersten Spiel-Äußerung.

## Fenster-Schutz auch im Beat-Planer (20.08., „c'est également valable pour le Rohschnitt“)

Die Klappen-/Einstiegs-Schutzregeln der Sichtungs-Fassung gelten jetzt an der QUELLE (`beats._spiel_grenzen` +
`schnittplan._spiel_fenster`), nicht nur im Agent-Tool: Produktions-Sprech mitten im Take schiebt den Einstieg nie
mehr hinter das Spiel, und der Einstieg liegt nie hinter der ersten Spiel-Äußerung. Takt komplett neu berechnet →
**Feinschnitt v8** (31 Segmente, 435,7 s): Szene-4-Eröffnung kommt jetzt aus 4.3 T1 **19,8–28,7 s** („Geh doch an!“-
Fenster) statt aus einem kaputten 38–39-s-Fenster. Szene 2 unverändert (war schon korrekt).

## Alternativen-Stapel V2/V3+ (20.08., Nutzer-Logik „Optionen ÜBER dem Master zeigen“)

Der Agent legt die besten Passagen ANDERER Takes als **stumme Alternativen** über den Master — am jeweiligen Beat
ausgerichtet (Beat-Matrix), gestapelt V2/V3 (Rangfolge: Anker > Anker-Score > Stärke, max. 2/Beat konfigurierbar).
Der Cutter vergleicht per Spur-Ausblenden, behält die beste, löscht den Rest — die höchste Spur spielt.
- **Automatisch** im Feinschnitt (`erzeuge_schnittplan` → `alternativen_fuer_plan`, Parameter `alternativen`/
  `alternativen_pro_beat`; Eintrag-`art="alternative"`, `spur≥2`, `video_only`, absolutes `tl_start`). Am Korpus:
  v9 = 31 Master-Segmente + 46 Alternativen.
- **Auf Zuruf** via Tool `lege_alternativen(szene?, beat?, max_pro_beat?)` → insert-Kommandos als Proposal (additiv,
  Vorschau/Annehmen/Verwerfen); stapelt auf die NÄCHSTE FREIE Spur über der aktuellen Timeline (Snapshot-basiert).
- Infrastruktur dafür: `InsertCmd.videoOnly` · `LoadSequenceCmd`-Segmente mit `start`/`videoTrackIndex`/`videoOnly`
  (Overlays in Generator-Proposals — schließt Task „loadSequence-Spuren“ teilweise) · Editor-Loader honorieren
  `track: v2/v3` + `spur` und wachsen `numVideoTracks` funktional (Bugfix: stale-State ließ V3 unsichtbar) ·
  Kollisions-Regel von insert bleibt (belegte Spur → ans Spur-Ende geschoben).
Tests: S11 (Agent wählt Tool, Kommandos videoOnly/V≥2) · Browser (v9-Restore: V2/V3 stumm, Vorschau/Verwerfen
exakt) · pytest 15 Validator-Fälle.
**Nachbefund (Nutzer, Schwarzbild):** Alternativen AUFEINANDERFOLGENDER Beats überlappten auf derselben Spur
(12-s-Fenster > Beat-Abstand) → das Timeline-Modell verweigert Überlappungen → Engine leer → Schwarzbild.
Fix: **Spur-Zuteilung** in `alternativen_fuer_plan` — eine Alternative kommt nur auf eine an ihrer Position freie
Spur (sonst nächste, sonst entfällt sie). v9 neu erzeugt: 73 Segmente (28 V1 · 25 V2 · 17 V3), überlappungsfrei
verifiziert (SQL-Check + Browser-Load ohne Modell-Fehler, Videos ready).

## Wiedergabe-Semantik des Alternativen-Stapels (20.08., Nutzer: „Stapel kapert Bild UND Ton“)

Befund: „oberste Spur gewinnt“ galt für Bild UND Ton zugleich — eine stumme Alternative oben machte die Timeline
lautlos und verdeckte den Master dauerhaft. Zwei Regeln stellen die richtige Semantik her:
1. **Audio-Fallthrough (Engine):** gewinnt ein stummer Overlay (videoOnly) oder eine per M gemutete Spur das Bild,
   kommt der TON weiter von der obersten darunterliegenden sichtbaren Spur mit Ton — getrieben über den AudioPool
   (gleicher Mechanismus wie Ton-Brücken), inkl. Fades/Gain. Auditionieren einer Alternative = Bild wechselt,
   Master-Ton läuft weiter.
2. **Alternativ-Spuren standardmäßig AUSGEBLENDET** nach jedem Timeline-Load (`versteckeAlternativSpuren`: Lanes > V1,
   die ausschließlich stumme Overlays tragen): Default-Wiedergabe = exakt der Master. Zum Vergleichen blendet der
   Nutzer V2/V3 per Auge ein.
Browser-verifiziert: nach Load sind v2/v3 hidden; V2 eingeblendet + Playhead auf 41 s → sichtbares Video = Alternative
(gemutet, korrekte Quellzeit 50,5 s), Master liegt als Ton-Quelle darunter, Wiedergabe ohne Konsolen-Fehler.

## Aktions-Vorgriff (20.08., Nutzer: „2.2 T4 beginnt schon sitzend — das Spiel beginnt viel früher“)

Befund am Take T007: das Tee-Servieren (15–23 s, „Hier.“ „Bitte schön.“) IST die Aktion des Beats B2 („comes back with
two cups“ + Z4), aber Kurzrufe tragen per Design null Beat-Evidenz und die VQA-Fragen zu A3 waren nicht diskriminant
(„Tisch vor dem Sofa?“ → überall ja → Gewicht 0) → B2 begann erst an der Anker-Zeile (38,8 s), das Servieren fiel weg.
Fix in `beats.takt_fuer_take`: **Aktions-Vorgriff** — trägt ein Dialog-Beat Skript-Aktionen vor seiner Replik und liegt
direkt vor seinem Fenster unbeanspruchtes Spiel (Kurzrufe/Bewegung, von keinem Evidenz-Fenster belegt), greift der Beat
dorthin zurück (Grenzen: letztes Evidenz-Fenster, Spielbeginn, 30 s); verschluckte Durchgangs-Fenster werden gekürzt.
Der Planer erweitert das Dialog-Cap um den Vorgriff und schützt den neuen Einstieg (sonst hätte die Kappung ihn wieder
entfernt). **Feinschnitt v10:** Segment Nr9 = 2.2 T4 **15,1–69,4 s** (statt 38,8) — Ankommen mit den Tassen, Servieren,
„Babe? … Musst du nicht langsam mal aufstehen?“; Szene-2-B3 bekam analog +2 s. 73 Segmente, überlappungsfrei,
Editor-Load ohne Modell-Fehler, Alternativ-Spuren default versteckt.

## Phasen-Schnittstelle beim Take-Wechsel (20.08., Nutzer: „stehend über Yuri → Schnitt → wieder sitzend“)

Regel (vom Nutzer bestätigt): **Die Intensität darf über eine Coupe innerhalb der Szene nie zurückfallen.** Endet ein
Segment mit ambivalenten Wiederholungen/Rufen (weiche Anker < 0,66, „Hallo?/Hey!“) — physisch schon der Beginn des
nächsten Beats — und kommt der nächste Beat aus einem ANDEREN Take, wird das Segment nach seiner letzten HARTEN
Anker-Zeile + 3 s beendet; die Eskalation spielt der nächste Take in der richtigen Intensität. Gleicher Take → kein
Schnitt. Implementiert als Post-Pass in `_plane_szene_beats`, Beleg im Prüfbericht („Phasen-Schnitt: Ende bei X statt Y“).
**Feinschnitt v11:** 3 Phasen-Schnitte — Sz2 Nr9 endet 49,9 s (sitzend, nach „Hey, Babe?“) statt 69,4 s (stehend),
Nr10 straff 80,2–87,8, Sz5 B8 113,9 statt 121,3. 73 Segmente / 504 s, überlappungsfrei, Editor-Load fehlerfrei.

## V1 = Rough Master (20.08., Nutzer: „die Master-Line darf diese zerschnittenen Dinge nicht enthalten“)

Spur-Architektur neu geordnet: **V1 = durchgehender Rough Master** (nur Master-Segmente, keine Inline-Fragmente) ·
**V2 = Schnitt-Overlays** (Cutaways + Reaktionen, video_only, standardmäßig SICHTBAR — sie sind Teil des Schnitts) ·
**V3+ = Alternativen** (standardmäßig ausgeblendet). Cutaways/Reaktionen zerschneiden den Master NICHT mehr
(kein teil1/teil2-Split), sondern liegen als Overlay über ihm (`spur=2`, `overlay_offset` relativ zum Parent-Segment);
die **Ton-Brücken entfallen komplett** — der Master-Ton läuft dank Audio-Fallthrough von selbst unter jedem Overlay
weiter. Loader blendet Alternativ-Lanes aus und Overlay-Lanes EXPLIZIT ein (klebriger hidden-Zustand aus früheren
Sitzungen wird überschrieben; Flag `alternative` wird in den Timeline-Segmenten persistiert). **Feinschnitt v12:**
Szene 2 auf V1 = 4 durchgehende Segmente (statt 7 Fragmente mit Brücken); 68 Einträge = 22 V1 · 3 V2 · 43 V3/V4;
überlappungsfrei, Editor-Load fehlerfrei, V2 sichtbar / V3+V4 versteckt.

## Schluss-Auslauf (20.08., Nutzer: „im V1-Master die Szenen bis zum Ende laufen lassen — die Lamentation ist Gold“)

Regel: das LETZTE Master-Segment jeder Szene läuft bis zum Ende des Spiels — die stumme Handlung nach der letzten
Zeile (Weinen, Umarmen = der Skript-Schluss) bleibt im Rough Master. Grenzen setzen NUR echte Gegen-Signale:
Produktions-/Slate-Sprech nach dem Ende, sichtbarer Ausstieg (Bewegungssprung, Baseline = Anfang des Auslaufs selbst,
denn Weinen ist dort die Norm), Clip-Ende. Wichtiger Befund dabei: `_spiel_grenzen` endet am letzten GESPROCHENEN
Wort — für den Auslauf ist das falsch, Ziel ist das Clip-Ende. Die Schluss-Beats (B5 „crying and hugging“ …), die
mangels Bild-Beleg nie belegt wurden, gelten damit als gedeckt (Protokoll statt Lücke). **Feinschnitt v13:** Sz2
110,8→151,6 s (40 s Lamento) · Sz3 169,5→185 · Sz4 41→67 · Sz5 29,9→51,6 (Schlussbild des Films). 608 s gesamt,
überlappungsfrei, Editor-Load fehlerfrei.

## Auslauf-Wächter + dramaturgisch konsistente Spuren (20.08., Nutzer-Befunde am v13)

1. **Wiederauferstehungs-/Crew-Wächter im Schluss-Auslauf:** Ende beim frühesten Signal — Produktions-Sprech ·
   Bewegungs-Bruch (Schwelle = 2,5 × Median der Lamentations-Baseline, Boden 4 % — der alte 3×-Kern-Schwellwert
   übersah das Aufstehen) · **Gesicht, das nach ≥ 10 s Abwesenheit wieder auftaucht**, sofern die Abwesenheit IM
   Segment begann (die „Tote“ hebt den Kopf, Crew tritt ins Bild; T007: Spans enden 130/135 → neue ab 145 → Ende
   144,5 s). Beleg: „Ende durch Gesicht nach 15s Pause wieder erkannt …“.
2. **V2 nur echte Einzel-Reaktionen:** Zweier-„Gegeneinstellungen“ derselben Paarung sind Achsen-Sprünge ohne
   Mehrwert → kein Overlay mehr (Szene-2-Zweier entfiel).
3. **Alternativ-Spuren je Szene = EIN Kamerawinkel:** Lane-Vergabe nach Wichtigkeit der Einstellung (Anker-Anzahl >
   Menge), jede Einstellung behält ihre feste Spur (V3 = 2.1, V4 = 2.2 …) — eine Spur einblenden = ein konstanter
   Blickwinkel, kein Links/Rechts-Zapping. Einstellungen über dem Budget entfallen (Konsistenz vor Menge).
**Feinschnitt v14:** 50 Segmente / 581 s — Sz2-Lamento bis 144,5 s (Aufwachen + Crew raus), 22 V1 · 2 V2 · 21 V3 · 5 V4,
überlappungsfrei, Editor-Load fehlerfrei.

## Anspiel-Barriere (20.08., Nutzer erklärt die Dreh-Praxis: vorgespielter Szenenanfang zum Warmwerden)

Befund 3.2/T3: gewollt war nur „Fred steht auf, dreht sich — ‚Wir brauchen dich' — geht“; davor haben die Darsteller
den Szenenanfang ANGESPIELT (wieder reinkommen, hinsetzen). Der Take trägt den Marker im Transkript: Klappe → „Set.“
@8,6 → 34 s stummes Anspielen → **„Bitte.“ @43,5 (Regie startet den gewollten Teil)** → Z11 @50,3. Der Aktions-
Vorgriff griff über das „Bitte.“ zurück → im Master erschien das Wieder-Reinkommen nach der laufenden Diskussion.
**Regel:** der letzte Slate-/Produktions-Sprech VOR der ersten Spiel-Äußerung ist eine HARTE Grenze — egal wo im
Take (die alte Erste-Hälfte-Heuristik gilt nur noch für stumme Takes). Implementiert in `beats._spiel_grenzen` +
`schnittplan._spiel_fenster` → wirkt auf Beat-Fenster, Vorgriff, Alternativen und Sichtungs-Fassung zugleich.
**Feinschnitt v15:** Sz3 Nr14 = 3.2 T3 **47,4–53,8** (Aufstehen → „Wir brauchen dich“ → Abgang) statt 23,8–53,8;
Tee-Servieren (T007) unverändert 15,1 s. 50 Segmente / 565 s, überlappungsfrei.

## Anschluss-Auslauf beim Take-Wechsel (20.08., Nutzer: „die Szene hätte bis hierher weiterlaufen müssen“)

Das Spiegel-Stück zum Phasen-Schnitt: enthält der SCHEIDENDE Take auch den ersten Beat des nächsten Segments
(seine Anker-Zeile fällt dort später), läuft das Segment durch die **stumme Anschluss-Handlung** weiter (Aufstehen,
Gang zur Tür — Sz3: Bewegungs-Spike 116–118 s in 3.1/T4) bis kurz vor die eigene Anker-Zeile; die Replik übernimmt
der neue Take = **Match auf die Bewegung**, die Coupe wird unsichtbar. Nur reine Aktions-Brücken (keine alignierten
Zeilen — sonst Phasen-Schnitt-Domäne; die zwei Regeln schließen sich per Konstruktion aus), nur mit echtem
Bewegungs-Moment, max. 20 s. **Feinschnitt v16:** Sz3 Nr13 = 80,9–**119,3** (statt 109,2): „…besser gehen“ →
er steht auf, geht zur Tür → Schnitt → an der Tür dreht er sich: „Wir brauchen dich“ (3.2/T3). 575 s, überlappungsfrei.

## Spielende = Clip-Ende (20.08., Nutzer: „Freds Dialog wurde abgeschnitten, er hat nicht ausgeredet“)

Wurzel: die Spiel-Fenster endeten am **letzten transkribierten Wort** (`spiel_ende_s`) — leise gesprochenes Kleinzeug,
das dem ASR entgeht, und die Abgangs-Handlung danach (3.2/T3: Freds „blabla“ 54–56 s + Abgang durch die Tür 57–58 s)
waren für ALLE Fenster unerreichbar. Fix in `_spiel_grenzen`: Ende = Clip-Ende, begrenzt nur durch Produktions-Sprech
NACH der letzten Spiel-Äußerung; was wirklich ins Segment kommt, entscheidet der bewegungsbasierte Nachlauf.
Zwei Folge-Regressionen dabei gefunden und behoben (die Batterie der 5 Sentinel-Segmente Sz2-Nr9/Sz3-Nr13/Nr14/
Sz5-Nr19/Nr21 muss GLEICHZEITIG stimmen):
- **Anker-Kette v2 im Phasen-Schnitt:** eine harte Zeile mit NEUER Skript-Zeile verlängert die Kette immer
  (Dialog-Progression, Sz3-Aktionsbrücke, Sz5-Tirade); eine harte WIEDERHOLUNG einer schon gekesteten Zeile nach
  > 12 s ist Eskalation und verlängert nicht (Sz2: viertes Z4 „hörst du mich?!“ nach 43 s Gerufe).
- **Beat-Modus ohne generisches Ende-Bereinigen:** der alte Heuristik-Trim fraß den skriptgemäßen ABGANG als
  vermeintlichen „Ausstieg“; Enden gehören jetzt allein Phasen-Schnitt/Anschluss-/Schluss-Auslauf samt Wächtern.
**Feinschnitt v18:** 3.2/T3 = 47,4–59,2 (Replik vollständig + „blabla“ + Abgang); 49 Segmente / 566 s, überlappungsfrei.

## Bekannte Grenzen / nächste Schritte

- `swap_beat_source` + `regenerate_schnittplan` liefern eine **flache** loadSequence (audio_only-Brücken entfallen,
  video_only wird normales Segment) — für Feinschnitte mit Cutaways degradiert der Vorschlag leicht; die volle
  Schichtung kommt beim Neuladen des Editors (persistierte Timeline). Folge-Pass: loadSequence um Spur/Overlay erweitern.
- Ein Modell-Upgrade (größeres lokales Modell / natives Tool-Calling) ist vorbereitet, aber nicht nötig gewesen:
  qwen2.5:14b besteht alle Szenarien mit den neuen Leitplanken.
- Proaktivität (Agent meldet Lücken/Widersprüche von sich aus) = bewusst offen.
- Latenz im Chat: ~50 s (Frage) bis ~3 min (Bearbeitung mit 31-Clip-Snapshot, 4 ReAct-Schritte) auf dem Mac mini —
  Prompt-Verarbeitung dominiert. Hebel: Ollama-KV-Cache über identische Prompt-Präfixe / kleineres Snapshot-Format.
