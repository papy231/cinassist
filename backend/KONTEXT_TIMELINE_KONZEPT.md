# Kontext-Schicht → Timeline-Vorschlag — Konzept (Stand 2026-08-19)

> Ausgangslage: Ebene 0 (Sync/Takes) und Ebene 1 (Clip-Fakten: Transkript mit Sprechern und Wortzeiten,
> Stichproben-Bilder, Personenzahl, Framing) stehen für alle 58 Clips. Was fehlt, ist **Verstehen**:
> *was passiert in jeder Szene, wer ist da, was ist die Geschichte, was soll am Ende auf der Timeline
> stehen?* Ohne diese Schicht kann kein Modell einen Schnitt vorschlagen — egal wie man promptet.

## 0. Der Kern in einem Satz

Ein Schnittvorschlag ist eine **Abbildung von Erwartung auf Material**:
`Was soll erzählt werden (Drehbuch/Intention)` × `Was wurde gedreht (Takes, Fakten)` → `Auswahl + Reihenfolge`.
Beides muss explizit im System liegen, als **prüfbare Daten**, nicht als Prompt-Prosa.

## 1. Was das Material heute schon verrät (Befund am Korpus)

Ein 20-Zeilen-Prototyp (Slate-Parser + Produktions-Sprech-Filter über die Transkripte) zeigt:

| Befund | Beleg | Konsequenz |
|---|---|---|
| **Die Kamera-Nummerierung (`S004_S004_T002`) ist NICHT die Drehbuch-Nummerierung.** Die echte steht auf der gesprochenen Klappe: „Scene 5.2.1, Take 1“, „Szene 3.2, Teil 3“, „4.1, Day 2“ (Whisper hört „Take“ als Date/Day/Teil) | 43/58 Clips mit parsebarer Sprech-Klappe; `S003_S001_T001` = **4.1/T4**, `S004_S004_T002` = **5.2.1/T1**, `S005_S002_T012` = **5.1.1/T1** | Die Szenen-Struktur der Geschichte (2.1, 2.2, 3.1, 3.2, 4.1–4.4, 5.1.1, 5.2.1–5.2.4) ist **deterministisch** aus dem Ton ableitbar — kein LLM nötig. Die Ordner „Szene 4/Einstellung 3“ aus dem Dateinamen sind teilweise falsch. |
| Transkripte enthalten **Produktions-Sprech** („Kamera läuft“, „Set“, „Bitte“, „Danke“, „Sorry, nochmal auf Anfang“) neben dem Spiel | 1–4 solche Segmente pro Take, am Anfang/Ende | Muss **getrennt** werden: Spiel-Dialog = Story; Produktions-Sprech = Metadaten (Start des Spiels, Abbruch, Wiederholung = Take-Qualität). |
| Viele Takes haben **kein Spiel-Dialog** | 4.1 (99–112 s, 0 Zeilen), 4.3 (≈0), 5.2.2–5.2.4 (0) | Diese Szenen sind **Handlung ohne Worte** — Verstehen kommt nur aus der Bildspur über die Zeit (Stichproben-Beschreibungen) + Drehbuch. Ein Sprach-only-Ansatz wäre blind. |
| Mehrere Takes einer Szene wiederholen **denselben Text** | 2.1/2.2: „Babe, musst du nicht langsam aufwachen?“ in 6 Takes; 3.2: „Wir brauchen dich, jetzt noch mehr als zuvor“ in 3 | Der **Konsens-Dialog** über Takes hinweg ≈ Drehbuchzeilen (rekonstruierbar auch ohne Skript); 2.1 vs. 2.2 = verschiedene Einstellungen derselben Szene → **Coverage**. |
| Szene 1 = 13 stumme Inserts (Pillendose, Löffel, Fotos, Gitarren, Poster) | S001_* ohne Ton, 4–37 s | Inserts gehören **keiner** Dialogszene — sie werden später Motiven/Szenen zugeordnet (Löffel+Pillendose ↔ Szene 2 „aufwachen“). |
| Take-Abbrüche sind hörbar | 2.2/T2: „Können wir den direkt nochmal machen? … Sorry“ (37 s) · 4.1/T1 „Mach nochmal auf Anfang“ (19 s) | **NG-Takes** erkennbar: kurz + Abbruch-Sprech → niedrige Priorität im Vorschlag. |

Vorläufige Story (nur aus Spiel-Dialog, ungeprüft, als Beispiel dessen, was die Schicht leisten muss):
Szene 2 — eine Person findet Yuri regungslos („Babe, musst du nicht aufwachen?“ → „Yuri! Bleib bei mir!“). Szene 3 —
Gespräch über Band/Album („Wenn das Album raus ist …“, „Wir brauchen dich“). Szene 4 — eine Person allein,
ein Gerät funktioniert nicht („Geh doch an!“). Szene 5 — jemand vor Ophelias Tür („es geht um die Band“),
dann „Du bist nicht echt … Yuri, es ist alles meine Schuld“, „Komm zurück, lass mich nicht wieder allein“.
Figuren: **Yuri, Ophelia** („Babe“ ist Kosename, kein Name — Nutzerangabe). Motiv: das Versprechen („Pinky Promise“, „unser Scheißversprechen“).

## 2. Die fehlende Hälfte: die Erwartung

Alles oben ist *Material-Seite*. Die *Erwartungs-Seite* — was erzählt werden SOLL — ist für einen
Spielfilm das **Drehbuch** (und, wenn vorhanden, Shotlist/Drehplan/Regie-Notizen mit „circled takes“).
Damit wird aus Raten Abgleich:

- **Skript-Alignment**: jede Transkript-Zeile wird einer Drehbuchzeile zugeordnet (Sequenz-Alignment,
  fuzzy, pro Szene). Ergebnis: *welcher Take deckt welche Zeilen*, *welche Zeilen fehlen komplett*,
  *wo weicht das Gespielte ab* (Improvisation vs. Textfehler). Das ist das Prinzip von Avid ScriptSync /
  DaVinci „Text-based Editing“ — aber hier mit Sync-Ton und Sprecherzuordnung.
- **Szenenreihenfolge** = Drehbuchreihenfolge (Drehtag-Reihenfolge ist irrelevant für die Story).
- **Regieanweisungen** erklären die stummen Takes (4.1, 4.3, 5.2.2 …), die das Bildmodell nur beschreiben,
  nicht deuten kann.

Gibt es **kein** Drehbuch, arbeitet die Schicht im **Rekonstruktions-Modus**: Konsens-Dialog über Takes
= Skript-Ersatz, Szenenreihenfolge aus Slate-Nummern, stumme Szenen aus Bildfolgen. Schwächer, aber
dieselben Datenstrukturen — und der Nutzer korrigiert im UI (Korrekturen werden zu Fakten).

## 3. Schichtenmodell (jede Schicht nur aus Belegen der darunterliegenden)

```
L0 Assets/Takes/Sync        (fertig)  Video ↔ WAV, Offsets, Multicam
L1 Clip-Fakten              (fertig)  Transkript (Wörter, Sprecher), Frames+Beschreibungen, Personen, Framing
L2 Take-Kontext             NEU       Slate (Skript-Szene/Take), Spiel vs. Produktion, Spielbeginn/-ende,
                                      Abbruch/NG-Signale, Dialogzeilen normalisiert (wer sagt was, wann),
                                      Bild-Verlauf (Beschreibungen in Zeitfolge), Deckung der Skriptzeilen
L3 Szenen-Kontext           NEU       pro Skript-Szene: Ort, Figuren, Beats (was passiert, in Reihenfolge),
                                      Konsens-/Skript-Dialog, Coverage-Matrix (Einstellung × Zeile),
                                      Take-Ranking mit Gründen, offene Fragen
L4 Story-Kontext            NEU       Figuren + Beziehungen, Szenenfolge, Arc/Wendepunkte, Motive,
                                      Inserts→Szenen-Zuordnung, Ton (Genre/Stimmung laut Skript)
L5 Schnitt-Plan → Timeline  NEU       pro Szene: Zeilen → (Take, In, Out) + Einstellungswechsel-Regel,
                                      Inserts, Übergänge; jede Entscheidung mit Beleg; exportiert als
                                      Timeline (Rohschnitt) in den Editor, editierbar
```

Regeln, die in jeder Schicht gelten:
1. **Deterministisch vor LLM.** Slate, Produktions-Filter, Zeilen-Alignment, Coverage, Take-Dauer/Abbruch,
   Konsens-Dialog sind Algorithmen (Regex, Sequenz-Alignment, Clustering). Das LLM fasst zusammen, benennt
   Beats, schlägt Reihenfolge/Auswahl **unter** diesen Constraints vor — und zitiert Belege.
2. **Alles ist Daten, alles ist korrigierbar.** Jede Ebene hat eine UI-Karte; eine Nutzerkorrektur
   (z. B. „das ist Szene 5.1.1, nicht 5.5.2.2“) wird persistiert und gewinnt gegen die Automatik.
3. **Unsicherheit ist ein Feld, kein Geheimnis.** `unsicher`/`hinweise` wie heute im Bericht, pro Ebene.

## 4. Datenmodell (Vorschlag, additiv)

- `takes`: `+ skript_szene` (String „5.2.1“), `+ skript_take` (Int), `+ slate_quelle` (audio|dateiname|manuell),
  `+ spiel_start_s`/`spiel_ende_s`, `+ ng_signal` (JSON: abbruch, kurz, wiederholung), `+ bewertung` (manuell: circled/ok/ng)
- `dialog_zeilen` (NEU): `take_id, nr, start_s, end_s, sprecher, text, art (spiel|produktion|slate),
  skript_zeile_id (nullable), aehnlichkeit`
- `skript_szenen` (NEU, aus Drehbuch-Import oder Rekonstruktion): `nummer, titel, ort, zeit, figuren[],
  regieanweisung, reihenfolge`; `skript_zeilen`: `szene_id, nr, figur, text`
- `szenen_kontext` (NEU, L3): `skript_szene, zusammenfassung, beats JSON, figuren[], ort, coverage JSON,
  take_ranking JSON, belege[], unsicher[], manuell_geprueft bool`
- `story_kontext` (NEU, L4, eine Zeile pro Projekt): `figuren JSON, szenenfolge[], arc, motive[], inserts JSON`
- `schnittplan` (L5): `szene, eintraege [{zeile_von, zeile_bis, take_id, in_s, out_s, grund, beleg}]` → Timeline

## 5. Umsetzungsreihenfolge (jede Stufe einzeln abnehmbar)

1. **L2 deterministisch** (1–2 Tage): Slate-Parser (Audio + Dateiname, Konfliktanzeige), Produktions-/Spiel-
   Trennung, Spielbeginn, NG-Signale, Dialogzeilen-Tabelle, Bild-Verlauf. Ordner/Sortierung nach Skript-Szene
   (Option, statt Dateiname). UI: Take-Karte zeigt „5.2.1 · Take 1 · Spiel 12 s–95 s · Abbruch bei 37 s“.
2. **Skript-Import + Alignment** (2 Tage, wenn Drehbuch vorhanden): PDF/Fountain/FDX → `skript_szenen/zeilen`;
   Alignment Transkript↔Skript pro Szene; Coverage-Matrix; ohne Skript: Konsens-Rekonstruktion.
3. **L3 Szenen-Kontext** (2 Tage): Aggregation + qwen-Zusammenfassung mit Belegpflicht (wie Bericht), Take-
   Ranking (Vollständigkeit der Zeilen, Abbruch, Länge, manuelle Bewertung), UI „Szenen“ mit Korrektur.
4. **L4 Story-Kontext** (1 Tag): Figuren/Arc/Motive/Inserts — aus L3 + Skript; eine Seite, editierbar.
5. **L5 Schnittplan → Timeline** (3–4 Tage): Regelwerk Rohschnitt (Master-Take als Rückgrat, Einstellungs-
   wechsel an Sprecherwechseln/Reaktionen, Inserts an Motivstellen, Szenenübergänge), LLM nur für
   Auswahl-Entscheidungen zwischen gleichwertigen Optionen; Export in die Editor-Timeline (A/V-Segmente mit
   Sync-Ton), mit „Warum diese Wahl“ je Clip. Danach iteratives Feedback („anderen Take“, „länger“).

## 6. Offene Eingaben (vom Nutzer)

- **Drehbuch** („Pinky Promise“) als Datei — entscheidend für Stufe 2 und für die stummen Szenen.
- Shotlist/Drehplan/Regie-Notizen (circled takes), falls vorhanden.
- Bestätigung der Figurenliste: Yuri, **Babe (Eigenname)**, Ophelia — weitere?
- Stil-Ziel des Rohschnitts: klassische Dialog-Coverage (Master → Schuss/Gegenschuss) oder etwas anderes?


## 7. Umsetzungsstand (2026-08-19, abends)

Gebaut und am Korpus verifiziert (Drehbuch „Pinky Promise“, 5 Szenen, englisch; Dreh deutsch):

| Schicht | Modul | Stand |
|---|---|---|
| Skript-Import | `core/skript/parser.py` | PDF/TXT/Fountain → Szenen (Nr/INT·EXT/Ort/Zeit) + Zeilen (dialog/aktion/uebergang, Figur, Regie); Seitenumbrüche kein Absatzende. 5/5 Szenen, 24 Dialogzeilen korrekt. |
| Übersetzung | `core/skript/uebersetzung.py` | Dialogzeilen EN→DE via qwen (einmalig, editierbar, `text_ziel_quelle`). |
| L2 Klappe/Spiel | `core/skript/klappe.py` | Sprech-Klappe (Whisper-Varianten Take/Teil/Date/Day), Produktions-Sprech vs. Spiel (Zwei-Pass: „Bitte“ im Spiel bleibt Text), Satz-Einheiten über Wortzeiten, NG-Signale (Abbruch/kurz). **41/58 Klappen aus dem Ton**, Rest Dateiname/Inhalt. |
| L2 Alignment | `core/skript/alignment.py` | **bge-m3** (Ollama) sprachübergreifend + Lexik gegen die Übersetzung; Konsens aus monotoner DP und sicheren argmax-Treffern; kurze Einheiten strenger. Szene 3: 7/7 Zeilen exakt; Szene 2: 4/4; 5.1.1 ↔ Tür-Dialog, 5.2.1 ↔ Geist-Dialog sauber getrennt. |
| L2/L3/L4 | `core/skript/kontext.py` | Take-Kontext (Klappe, Einstellung, Spiel-Fenster, Zeilen→Skript, Bildverlauf, Abdeckung), Szenen-Kontext (Coverage, **deterministisches Take-Ranking mit Gründen**, LLM-Zusammenfassung/Beats/Figuren mit Belegpflicht, **deterministisch gezählte Anrede-Namen** als Brücke Skript↔Dreh), Story-Kontext. |
| L5 Rohschnitt | `core/skript/schnittplan.py` | Skript-Reihenfolge · Master + Coverage-Wechsel an Sprecherwechseln · Verschmelzen zusammenhängender Zeilen · stumme Einstellungen an der richtigen Position · Inserts per Embedding zur Skript-Erwähnung · Lücken-Report. Erster Lauf: 26 Segmente, 9:41 min, 2 Lücken (Z11/Z12 Szene 5 nicht gefunden). |
| Tasks/API | `workers/kontext.py`, `api/skript.py` | Upload → Import-Job · Kontext-Job · Schnittplan-Job · Korrekturen (Klappe/Bewertung/Übersetzung). |
| UI | `components/SkriptPanel.tsx`, Tab „Skript & Kontext“ | Szenen mit Skript+Übersetzung, Kontext-Karte, Take-Ranking mit Gründen, Take-Detail (gesprochen → Skriptzeile, Bildverlauf, manuelle Klappe/Bewertung), Rohschnitt-Liste mit Grund/Beleg → **„In Timeline laden“** (In/Out aus dem Plan, Sync-Ton). |

**Nachgezogen (gleicher Abend):**
- Figuren-Zuordnung Skript↔Dreh jetzt **deterministisch aus dem Alignment** (`figuren_aus_alignment`: Skript-Anrede
  „Orpheus, are you there?“ ↔ gesprochene Anrede „Ophelia, bist du da?“ ⇒ ORPHEUS = Ophelia; EURYDICE = Yuri, 4 Belege);
  das LLM bekommt sie als Fakt. Zweiter Alignment-Pass mit Namensersetzung (Dreh-Name → Skript-Name) + Glossar-
  Angleichung („Offelia“ → „Ophelia“) → Szene-5-Zeile 1 wird jetzt gefunden. Reine Anrede-Einheiten („Babe?“)
  werden mit dem Folgesatz verschmolzen.
- **Stumme Einstellungen nach Handlung getrimmt** (`aktivitaet.py`: Anteil bewegter Pixel, 96×54, 2/s aus dem Proxy;
  aktives Fenster = nachhaltige Bewegung; > 75 s → aktivstes 75-s-Stück; ohne Bewegung → 10 s „statisch“). Beispiel
  5.2.2: 23–98 s statt 0–75 s; 4.1: Einrichten/Stillstand am Ende weg.
- **Rhythmus + Kadrage** im Dialog: länger als 28 s auf einer Einstellung → Wechsel auf eine andere deckende
  Einstellung, bevorzugt engere Kadrage (Nah > Halbnah > Totale, aus L1-Framing); Sprecherwechsel ebenso;
  schwache Treffer (< 0,62) einer Nicht-Master-Einstellung lösen keinen Wechsel aus (werden als Lücke gemeldet).
- Timeline-Persistenz: „In Timeline laden“ speichert serverseitig (stil `rohschnitt`) und merkt die zuletzt geladene
  Timeline; beim Start wird sie wiederhergestellt (ohne lokalen Verweis: der neueste Rohschnitt).

**Dritte Runde (Nutzer-Befund am v5: Doppel-Insert, Klappe im Bild, „Yuri steht auf und lacht“ am Take-Ende):**
- **Doppel-Takes eines Motivs** (Insert „Löffel“ T001/T002, CLIP-Kosinus 0,97) werden geclustert (≥ 0,90) und nur
  einmal verwendet — Vertreter: circled > späterer Take > länger; jede Skript-Erwähnung bekommt höchstens ein Motiv.
- **Sichtbare Klappe/Hand am Anfang**: Bewegungs-Spike in den ersten 12 s nach dem Fensterstart → Start nach dem Spike,
  wenn sich das Bild beruhigt (`anfang_nach_klappe`), für stumme Takes, Inserts und Dialogsegmente < 20 s.
- **Aus dem Spiel fallen am Take-Ende**: (a) Text-Seite — im letzten Drittel eine kurze Einheit nach ≥ 8 s Stille oder
  mit Ausstiegs-Wörtern (sorry, haha, danke, nochmal, okay …) oder eine **neue Sprecherstimme** (Crew) ⇒ ab dort
  Produktion; endgültig erst **nach dem Alignment** (eine Einheit mit sicherem Skript-Treffer — „Was war meine
  Schuld?“ nach 50 s Stille — ist kein Ausstieg). Beispiel 2.1/T2: „Oh, sorry.“ bei 196 s nach 17 s Pause → Spiel-
  Ende 180 s, Segment endet 180,7 s statt 197,6 s. (b) Bild-Seite — Bewegungssprung im letzten Viertel (Aufstehen)
  → Ende davor (`ende_bereinigen`). Mini-Schnipsel < 3 s werden gestreckt oder weggelassen.

**Vierte Runde („intelligenter“):** narrativ monotone Szenen-Teile (bei dreistufiger Klappe 5.1.x → 5.2.x kein Rücksprung,
bei zweistufiger 2.1/2.2 = Coverage, frei) · **dedizierte Einstellungen** (deckt eine Einstellung nur 1–3 Zeilen mit
sicherem Treffer, wurde sie dafür gedreht → wird dort verwendet, z. B. 3.2 „Wir brauchen dich“) · **Handlung zwischen
Zeilen** bleibt drin, wenn das Skript dort eine Aktion hat und der Take Bewegung zeigt (bis 45 s), sonst enger Schnitt ·
Dialog-Schnipsel < 2,5 s entfallen · jeder generierte Schnittplan wird sofort als Timeline (stil `rohschnitt`) persistiert,
der Editor lädt beim Start den **neuesten** Rohschnitt (wenn jünger als die zuletzt geladene Timeline).

**Feinschnitt (Modus `feinschnitt`, Button im Panel):**
- **Cutaways mit Ton-Brücke (L-Cut)**: in Dialogsegmenten ≥ 14 s wird an der größten Sprechpause (≥ 1,2 s zwischen zwei
  zugeordneten Sätzen; sonst bei 45 %) ein 2,5–3,5-s-Cutaway aus den kurzen stummen Detail-Einstellungen der Szene
  (Finger, Gesicht) gesetzt — Bild ohne eigenen Ton; der Master-Ton läuft als `audio_only`-Clip derselben Quelle
  lückenlos weiter. Die Engine wurde dafür um `videoOnly` ergänzt (Cutaway stummgeschaltet, Ton aus dem AudioPool).
- **Höhepunkte statt Blöcke**: Handlung ohne Dialog > 24 s → bis 3 Fenster à 8 s an Bewegungsmaxima (angrenzende
  verschmolzen), chronologisch, ≤ 24 s (reine Actionszene ≤ 40 s).
- Engere Handles (0,3/0,5 s), Fades 0,4 s an Szenengrenzen (Bild+Ton), Dialog-Schnipsel auf ≥ 2,5 s gestreckt.
- Jeder Eintrag trägt `tl_start/video_only/audio_only/fade_in/fade_out`; Persistenz als Timeline und beide Loader
  (Plan → Timeline, Verlauf → Timeline) respektieren sie. Feinschnitt v1 am Korpus: 42 Einträge, 6:00 (Rohschnitt 8:50).

**Skript-gesteuerte Bildprüfung (neu, Nutzer-Befund: Cutaway „Yuris Hand“ ohne Sinn, Reihenfolge der stummen Teile
fraglich → „das Skript ist verstanden, die Medien nicht gegen das Skript geprüft“):**
- Gemessen: llava:7b beantwortet Ja/Nein zu **Personen-Handlungen/Posen** zuverlässig (Gitarre spielen, sitzen, stehen),
  **kleine Requisiten** (Mülleimer, Lampe) oft falsch. ⇒ `core/skript/aktionen.py`: je Szene formuliert das LLM aus den
  Aktionszeilen 2–3 konkrete Körperhandlungs-Fragen (EN) + Label; VQA auf dichten Frames aller Takes der Szene (5 s stumm /
  10 s Dialog, 448 px, Cache je Clip); trennscharfe Fragen zählen voll, geteilte halb; Zweitsignal CLIP-Ähnlichkeit
  Frame↔Label. Ergebnis: `take_kontext.aktionen` (Zeitfenster je Skript-Aktion) und `szenen_kontext.aktions_coverage`
  (gedreht / unsicher / fehlt) — sichtbar in Szene-Karte, Take-Details und Prüfbericht.
- Schnittplan: stumme Takes werden nach **bestätigten Skript-Aktionen in Skript-Reihenfolge** gelegt (statt Klappen-
  nummer + Bewegung); Cutaways nur noch **skript-motiviert** (Aktionszeile zwischen zwei Dialogzeilen, im Bild belegt,
  aus DERSELBEN Szene, nie aus dem eigenen Master-Take) — die Rotation „irgendein Detail in die größte Pause“ ist weg.
- „Ordner nach Szenen sortieren“: Medien-Ordner = Skript-Szene (Klappe/Alignment), nicht Kamera-Dateiname.

Ergebnis Bildprüfung (alle 5 Szenen, ~3 h Rechenzeit, gecacht): Szene 2 A0/A3/A5/A7 gedreht, A2 unsicher, A9 fehlt; Szene 4
A0/A4 gedreht (Gitarre statt Schreiben, Geist hinter dem Sofa), A2/A3 nur schwach (Mülleimer/TV = kleine Objekte). Feinschnitt v2:
Szene 2 öffnet mit den Händen (2.4, A0 bestätigt), Szene 4 = Gitarre → „Geh doch an“ → Geist (4.3/T5, 7 Frames), Szene 5 = Tür →
Geist-Dialog → „wake up“-Aktion (1× statt 3×). Regeln dazu: eine bestätigte Aktion höchstens einmal pro Szene (bester Beleg),
Einzel-Frame-Treffer zählen nicht, Cutaways nur für Aktionen ohne eigenes Segment, Take-Wahl stummer Einstellungen nach Bild-Belegen.

**Gesichter ↔ Figuren (neu):** `core/skript/gesichter.py` — MTCNN + FaceNet (VGGFace2, offline) auf 1920-px-Frames aus dem
Original (im 960-px-Proxy sind Gesichter in Totalen ~25 px → unbrauchbar), alle 5 s, p ≥ 0,95, Breite ≥ 40 px; Greedy-Cluster
(Kosinus ≥ 0,50) + Verschmelzen (≥ 0,60); **Namen deterministisch aus dem Skript**: Präsenz-Matrix Figur × Szene (Sprecher-
Cues + Namen in Regie-Klammern) gegen Cluster × Szene (Anteil der Takes) → ORPHEUS = Ophelia (36 Takes, alle Szenen),
EURYDICE = Yuri (2/4/5), FRED (nur 3); Profil-Cluster werden an die ähnlichste benannte Person angehängt (≥ 0,45, eindeutig).
Persistenz `gesichts_cluster` (Thumb, Skript-/Film-Name, manuell änderbar) + `take_kontext.gesichter` (wer wann im Bild).
**Reaktionsschnitte im Feinschnitt**: lange Zeile (≥ 7 s) von A, aktuelle Einstellung zeigt Zuhörer B nicht, ein ANDERER Take
derselben Szene und desselben Szenen-Teils (andere Einstellung, kein Jump-Cut) zeigt B im Bild → 2,5 s auf B bei 60 % der
Zeile, Master-Ton läuft (L-Cut); Single (1 Person) = „Reaktion“, Zweier = ehrlich „engere Gegeneinstellung“. Jede Quelle nur
einmal. Feinschnitt v3: 3 solche Schnitte (Sz 2 ×2 Zweier auf Yuri, Sz 5 Reaktion der Geist-Yuri aus 5.2.3).

Verbleibende Grenzen: Gesichter im Profil/Gegenlicht werden nicht immer erkannt (→ „Sprecher nicht im Bild“ kann fehlen);
Fred hat keinen Film-Namen (wird nie angesprochen); Rhythmus folgt Sprechpausen und Bewegung, nicht dem Inhalt (keine Reaktionsschnitte in Schuss/Gegenschuss, kein Tempo nach
Inhalt); Takes mit zwei Klappen-Segmenten in einer Datei („3.1 … 3.2“) gelten als eine Einstellung; nicht gedrehte
Zeilen (Sz 5 Z11/Z12) bleiben Lücken — das ist korrekt, nicht zu „reparieren“.

**Sechste Runde (Nutzer-Befund am Feinschnitt v4, Szene 2: „die Logik wollte, dass du anfängst, wo sie schläft“):** Befund am
Take 2.1/T2: 46 s Spiel ohne Worte (Schlafen → Aufwachen), dann improvisiert „Oh, Scheiße“ (48 s) · „Fuck“ · „Babe?“, erst bei
70 s die erste alignierte Skriptzeile. Der Plan hatte A0 (Schlafen) aus 2.4/T1 genommen (dort VQA-„ja“), das Dialog-Segment bei
70 s begonnen → Sprung „schläft in 2.4 / spricht schon in 2.1“, der Moment des Aufwachens fehlte. Drei Regeln (`schnittplan.py`):
1. **Szenen-Eröffnung im selben Take** (`_szenen_eroeffnung`): hat der Take des ersten Dialog-Segments ≥ 8 s Spiel vor der ersten
   Zeile, kommen die Eröffnungs-Aktionen (Skript-Aktionen vor der ersten Dialogzeile) aus DIESEM Take: Einstieg = erste
   nachhaltige Bewegung (Aktivitätskurve, = Wecker/Aufwachen) minus 6 s Ruhe, frühestens nach der Klappe, Obergrenze
   `stumm_max`. Das dedizierte A-Segment aus einer anderen Einstellung entfällt dann (A0 gilt als gedeckt). Nur ohne
   Pre-Dialog-Spiel darf eine andere Einstellung die Eröffnung liefern.
2. **Improvisation zählt** (`_improvisation_davor`): nicht-alignierte Spiel-Sätze desselben Takes ≤ 30 s vor der ersten alignierten
   Zeile (bis Slate/Produktion/anderer Skriptzeile) gehören zum ersten Dialog-Segment der Szene.
3. **Vorlauf weniger VQA-abhängig** (`_vor_nachlauf`): rückwärts bis zum Beginn der zusammenhängenden Bewegung; Sprache (auch
   improvisiert) gilt als Aktivität; bis 3 s Ruhe unterbrechen nicht; max 10 s (Fein) / 18 s (Roh).
Ergebnis Feinschnitt v5 (Szene 2): 2.1/T2 **35,0 → 78,4 s** = 6 s Schlaf → Aufwachen (41 s, Frames geprüft) → „Oh, Scheiße“ →
„Babe?“ → „Ich geh uns … Tee machen“ in einem Bild; Beleg im Prüfbericht („Szenen-Eröffnung im selben Take: Spiel ab 8 s, erste
Bewegung bei 41 s, davor 6 s Ruhe → Einstieg 35 s statt 47 s — deckt A0“ · „Improvisiert vor Z1 eingeschlossen (23 s): …“).
Editor: Ripple-Trim standardmäßig (Alt = einfach), sichtbare Clip-Kanten, `reflow` klebt Sub-Frame-Überlappungen (Rundung) an,
ungültiges Modell blockiert den Editor nicht mehr (letzte gute Engine-Timeline + Toast).

**Siebte Runde — Beats statt Zeilen (Nutzer-Befund am v5, Szene 2: „2.2/T4 neben 2.1/T2 wirkt wie eine Wiederholung — dieselbe
Szene zweimal gespielt, anders gesagt“):** Diagnose aus den Daten: in 2.2/T4 ist Z4 viermal erkannt (40/46/64/89 s, „aufstehen /
Hey Babe / Wach doch auf / hörst du mich“), das Zeilen-Segment spannte 40→91 s und enthielt damit schon den nächsten Moment
(Sorge, „Yuri, geht's dir gut?“ = A5); der Plan sprang dann für Z6 nach 2.1/T2 @143 — exakt derselbe Moment (126–150 s „Geht's
dir gut? Yuri?“). Ursache: Planung nach *Skriptzeile*; eine Zeile ≠ ein Moment, viermal gesagt ≠ vier Momente. Lösung = **Beat-
Modell** (`core/skript/beats.py`):
- `beats_fuer_szene`: Aktionen vor der ersten Dialogzeile = Eröffnungs-Beat · jede Dialogzeile + die Aktionen seit der vorigen =
  ein Beat · Aktionen nach der letzten Zeile = Schluss-Beat · Szene ohne Dialog = ein Beat je Aktion (Sz2: B0 schläft/wacht auf ·
  B1 „Tee“ · B2 Tee + „aufstehen“ · B3 Sorge/schütteln · B4 Zusammenbruch · B5 weint/umarmt).
- `takt_fuer_take`: monotone Segmentierung jedes Takes in Beats (Viterbi). Ereignisse = alignierte Zeilen (Anker 1,5+Score),
  Improvisation (bge-m3 ≥ 0,48 + Marge → Beat-Gewicht; Rufe „Hey/Yuri?“ = Fortsetzung), VQA-Frames je 0,2 (innerhalb des
  Spiels). Start-Strafe klein (Takes beginnen mitten in der Szene: 3.2, 5.2.x), Skip 0,25, Vor-Strafe 0,05. Je Span: Kern
  (Sprache), lückenlose Grenzen, Vorlauf/Nachlauf nach Bewegung+Sprache, `evidenz` (Anker / eindeutige Improvisation / eigene
  Bild-Aktion / Eröffnung mit ≥ 6 s Spiel vor einer starken ersten Zeile), synthetischer B0 für den Master-Take (Regel 1 aus Runde 6).
  Persistenz `take_kontext.takt`, `szenen_kontext.takt`; Neuberechnung in „Kontext aufbauen“, nach der Bildprüfung und vor jedem Plan.
- `schnittplan._plane_szene_beats`: je Beat genau eine Quelle, genau einmal; Score = Anker 2,0–2,5 · semantisch 1,0 · Bild-Beleg
  0,6 (+Stärke bei Handlungs-Beats) · bester Take +0,5 / anderer −0,3 · Kontinuität gleicher Take +0,7 (entfällt > 28 s) ·
  Rhythmus-Wechsel +0,6 · Sprecherwechsel +0,4 · dedizierte Einstellung (≤ 3 Beats, < Hälfte) +0,8 · engere Kadrage +0,15/Rang ·
  **anderer Take derselben Einstellung −1,0 (Jump-Cut)** · schwacher Anker < 0,62 nur als Fortsetzung (nie Sprunggrund) ·
  Szenen-Teile monoton (5.1 → 5.2 kein Rücksprung, nur über Dialog-Anker gehoben). Aufeinanderfolgende Beats desselben Takes
  verschmelzen (Lücke ≤ 6 s, mit Skript-Aktion + Bewegung ≤ 45 s); Take-Wechsel nur an Beat-Grenzen, Einstieg am Beat-Beginn.
  Caps: Dialog-Beat 45/60 s (Anker bleiben, erst vorn kürzen), Bild-Beat 12/20 s um den Kern. Alter Zeilen-Modus bleibt über
  `beats=false` (Vergleich). Prüfbericht: **Beat-Matrix je Szene** (Beats × Takes, ★ gewählt, A/s/V/E-Evidenz, grau = Durchgang).
Ergebnis Feinschnitt v6 Szene 2: B0+B1 2.1/T2 35→79 (schläft → wacht auf → „Tee“) · B2 2.2/T4 39→91 (4× „aufstehen“) ·
B3 2.1/T4 50→97 (Sorge, schütteln) · B4 2.2/T4 92→111 („nein nein … bleib bei mir“) + Reaktion — jeder Moment genau einmal.
Szene 5: 5.1.1/T3 B0–B7 als ein Stück (einzige Einstellung des Teils; Wechsel zu anderem Take derselben Einstellung = Jump-Cut,
bewusst nicht), B8 aus T4, 5.2.1/T5 B10+B11 mit 30 s Handlung dazwischen, B12 aus 5.2/T4.

**Achte Runde — mehrdeutige Zeilen, Dauer-Prior, Wiederholungs-Wächter (Nutzer-Befund am v6, Szene 2: B2 aus 2.2/T4 und B3 aus
2.1/T4 waren fast wortgleich — dieselbe Aufführungsphase unter zwei Winkeln):** Wurzel: „Wach doch auf!“ / „hörst du mich?“
passen lexikalisch auf Z4 UND Z6; der Aligner zwang sie auf Z4, die B2→B3-Grenze in 2.2/T4 lag dadurch bei 91 s statt ~58 s,
B2 „verschluckte“ B3, der Plan holte B3 aus einem anderen Take → Wiederholung. Drei Maßnahmen (vom Nutzer freigegeben):
1. **Weiche Anker** (`beats._ereignisse`): ist ein anderer Beat dem GESAGTEN Text fast so ähnlich (bge-m3, Marge 0,06), wird die
   Anker-Evidenz verteilt (50 % eigener Beat, 35 % Alternativen) statt erzwungen; `anker_score` = originaler Alignment-Score
   (Bugfix: sonst fiel die Schwacher-Anker-Regel auf weiche Anker herein und B10 „Du bist nicht echt“ (0,82) verschwand).
2. **Dauer-/Positions-Prior** (Pass 2, semi-Markov-Näherung): erwartete Beat-Position aus den Skript-Textanteilen, skaliert auf
   den vom Take abgedeckten Beat-Bereich (Pass 1); Ereignis-Scores −0,9·|Position−Erwartung| → Grenzen wandern dorthin, wo das
   Spiel laut Drehbuch kippt; mehrdeutige Rufe folgen der Monotonie statt einem Beat alles zuzuschlagen.
3. **Wiederholungs-Wächter** (`schnittplan`): substanzielle Sätze (keine Rufe) jedes gewählten Segments werden gemerkt; ein
   Kandidat aus einem ANDEREN Take, dessen Sätze zu ≥ 50 % schon zu hören waren (lexikalisch ≥ 0,7 oder Embedding ≥ 0,85),
   wird übersprungen (Protokoll im Bericht); sind alle Kandidaten Wiederholung, entfällt der Beat ehrlich („Inhalt bereits …
   zu hören“). Ausnahme: Sätze, die auf Zeilen DES Beats aligniert sind (erwarteter Text — sonst blockte „Das kann nicht sein“
   (Z15) an Freds „Das kann doch nicht sein!“ aus B2).
Ergebnis Feinschnitt v7 Szene 2: B0+B1 2.1/T2 35→78 · B2 2.2/T4 39→69 (endet VOR der Panik) · B3 2.1/T4 82→103 (Sorge/
schütteln) · B4 2.2/T4 92→111 — Phasen sauber getrennt, kein Duplikat. Szene 5 unverändert korrekt (B10+B11 5.2.1/T5).

**Neunte Runde — KI-Agent an die Kontext-Schicht angeschlossen (2026-08-20, Nutzer: „un agent qui comprend vraiment
l'intention et agit vraiment“):** Diagnose: solide ReAct-Infrastruktur (qwen2.5:14b, SSE, ProposalStore mit Geister-
Vorschau), aber (1) blind für Drehbuch/Beats/Schnittplan, (2) fast keine Hände (nur Silences/Alt-Generatoren erzeugten
Proposals), (3) keine Chat-Historie, (4) keine Verifikation. Umsetzung in `backend/api/agent_kontext_tools.py` +
Änderungen in `agent.py`/`agent-trace.ts`/`ChatPanel.tsx` — 5 Wissens-Tools (get_script_overview / get_scene_context /
get_take_details / get_plan mit grund+beleg / search_transcripts), 3 Handeln-Tools (edit_timeline server-validiert →
Proposal · regenerate_schnittplan = echter L5-Planer · swap_beat_source über die Beat-Matrix), Projekt-Kontext +
tlIds im System-Prompt, Historie (8 Turns), Routing-Heuristik Frage/Bearbeitung, Zitierpflicht für Plan-Gründe,
Schleifen-Wächter (identischer Call ≤ 2×), Observation-Kompaktierung (4000 Zeichen), num_ctx 16k. Grundsatz:
**Der Agent wendet NIE direkt an — alles ist ein Vorschlag, der Nutzer akzeptiert im Editor.** Tests: 13 pytest-Fälle
für die Kommando-Validierung + 7 End-to-End-Szenarien gegen echte DB+Ollama (alle grün, inkl. Folgeauftrag über
Historie und Feinschnitt-Regeneration). Detail: `backend/AGENT_KONZEPT.md`.

**Zehnte Runde (2026-08-20, Nutzer: Sichtungs-Fassung + Fenster-Härtung):** (a) KI-Agent-Tool
`lege_sequenzen_chronologisch` v3 — Dedupe-Einheit ist der TAKE: ein Segment je EINSTELLUNG (bester Take), Szenen ohne
Dialog ein Segment je Motiv (CLIP-Cluster), Reihenfolge Szene→Teil→Einstellung, 27 Segmente/~19 min am Korpus.
(b) Fenster-Härtung an der Quelle (`beats._spiel_grenzen`, `schnittplan._spiel_fenster`): Einstieg nie hinter der
ersten Spiel-Äußerung (Produktions-Sprech mitten im Take, visueller Klappen-Skip gedeckelt), Ausstiegs-Bereinigung
≤ 15 s. Takt neu → Feinschnitt v8 (Szene-4-Eröffnung 4.3 T1 19,8–28,7 s statt 38–39-s-Bug). (c) Proposal-Workflow:
Vorschau (probeweise, nichts gespeichert) / Annehmen (speichert Sequenzen als Fassung) / Verwerfen (exakte Rückkehr).

**Elfte Runde (2026-08-20, Nutzer-Logik „Alternativen ÜBER dem Master“):** beste Passagen anderer Takes als stumme,
beat-ausgerichtete Alternativen auf V2/V3+ — automatisch im Feinschnitt (v9: 31 Master + 46 Alternativen) und auf
Zuruf (`lege_alternativen`, stapelt auf die nächste freie Spur). Editor: InsertCmd.videoOnly, loadSequence-Overlays,
Loader mit Spur-Unterstützung, funktionales numVideoTracks-Wachstum. Detail: backend/AGENT_KONZEPT.md.

**Zwölfte Runde (2026-08-20): Aktions-Vorgriff.** Beats mit Vor-Zeilen-Aktion greifen in unbeanspruchtes Spiel
(Kurzrufe/Bewegung ohne Beat-Evidenz) vor ihrer Anker-Zeile zurück (≤ 30 s, begrenzt durch Evidenz-Fenster/Spielbeginn) —
Befund: Tee-Servieren (15–23 s in 2.2/T4) fehlte, weil „Hier.“/„Bitte schön.“ als Kurzrufe keine Evidenz tragen und die
A3-VQA-Fragen nicht diskriminant waren. Feinschnitt v10: B2 = 15,1–69,4 s. Planer-Cap um Vorgriff erweitert.

**Dreizehnte Runde (2026-08-20): Phasen-Schnittstelle beim Take-Wechsel.** Nutzer-Regel: Intensität fällt über eine
Coupe innerhalb der Szene nie zurück. Segmente, deren Schwanz nur noch aus weichen Wiederholungen/Rufen besteht
(= physischer Beginn des nächsten Beats), enden vor dem Wechsel in einen anderen Take an ihrer letzten harten
Anker-Zeile (+3 s). Feinschnitt v11: Sz2 „Tee-Servieren“-Segment endet sitzend bei 49,9 s statt stehend bei 69,4 s.

**Vierzehnte Runde (2026-08-20): V1 = Rough Master.** Cutaways/Reaktionen zerschneiden den Master nicht mehr, sondern
liegen als stumme Overlays auf V2 (sichtbar, Ton-Brücken entfallen — Audio-Fallthrough); Alternativen wandern auf V3+
(default versteckt, `alternative`-Flag persistiert). Feinschnitt v12: Szene 2 = 4 durchgehende V1-Segmente.

**Fünfzehnte Runde (2026-08-20): Schluss-Auslauf.** Das letzte Master-Segment jeder Szene läuft bis zum Spielende
(stumme Lamentation nach der letzten Zeile bleibt im Rough Master); Grenzen nur durch Produktions-Sprech, sichtbaren
Ausstieg (Baseline = Auslauf-Anfang) und Clip-Ende. Deckt die nie bild-belegten Schluss-Beats. Feinschnitt v13:
Sz2 +40 s Lamento, Sz5-Schlussbild zurück; 608 s.

**Sechzehnte Runde (2026-08-20): Auslauf-Wächter + Spur-Dramaturgie.** (a) Schluss-Auslauf endet beim frühesten von:
Produktions-Sprech, Bewegungs-Bruch (2,5×Lamento-Median), Gesicht-Wiederauftauchen nach ≥10 s In-Segment-Abwesenheit
(Aufwachen der „Toten“/Crew — Sz2: 144,5 statt 151,6). (b) V2-Overlays nur Einzel-Reaktionen (Zweier = Achsensprung,
entfernt). (c) Alternativ-Lanes je Szene fest pro Einstellung, vergeben nach Anker-Gewicht. Feinschnitt v14.

**Siebzehnte Runde (2026-08-20): Anspiel-Barriere.** Dreh-Praxis (vom Nutzer erklärt): vor dem gewollten Teil wird der
Szenenanfang angespielt; die Regie startet den echten Teil mit „Bitte." mitten im Take. Regel: letzter Slate-/
Produktions-Sprech VOR der ersten Spiel-Äußerung = harte Grenze für alle Fenster (Beat, Vorgriff, Alternativen,
Sichtung). Feinschnitt v15: 3.2/T3 beginnt beim Aufstehen (47,4) statt beim wieder-Reinkommen (23,8).

**Achtzehnte Runde (2026-08-20): Anschluss-Auslauf (Match auf die Bewegung).** Spiegel-Regel zum Phasen-Schnitt:
enthält der scheidende Take den ersten Beat des nächsten Segments, läuft er durch die stumme Anschluss-Handlung bis
kurz vor die eigene Anker-Zeile weiter — der neue Take übernimmt die Replik. Nur zeilenfreie Brücken mit Bewegung,
max. 20 s. Feinschnitt v16: Sz3 „er steht auf und geht zur Tür“ (109→119) vor dem Schnitt auf 3.2/T3.

**Neunzehnte Runde (2026-08-20): Spielende = Clip-Ende.** Fenster endeten am letzten ASR-Wort → ASR-Lücken („blabla“
beim Abgehen) und Abgangs-Handlungen fehlten. Fix in `_spiel_grenzen` (Ende = Clip-Ende, nur durch Nach-Spiel-
Produktions-Sprech begrenzt) + Anker-Kette v2 (neue Zeile verlängert immer, Wiederholung nach >12 s = Eskalation) +
Beat-Modus ohne generisches Ende-Bereinigen. Feinschnitt v18: Freds Replik + Abgang vollständig (47,4–59,2).

**Zwanzigste Runde (2026-08-20): Rettungspass-Ton + Aktions-Beats (Nutzer-Befund „PPRM23_S004_S001_T003 fehlt —
die Narration kippt nach Minute 5“).** Drei-Schichten-Diagnose: (1) Silero-VAD übersieht distanzierte/hallige
Sprache (Rufe quer durch den Raum in der Totalen) → Whisper lief per `clip_timestamps` NUR über den Slate → 90 von
112 s des Takes waren transkript-leer, obwohl das verknüpfte WAV (`SZENE4-003.WAV`, Kanal-Mix, 0-dB-Peaks) die
gespielten Repliken („Die Scheiße, Mann!“, „Nein, nein, nein!“) klar enthält. (2) Szene 4 hat nur 1 Dialogzeile
→ 3 Beats; der Sammel-„schluss“ presste Mülleimer/Drogen, TV-Flackern und Geist in EINEN Slot. (3) Die
Alternativen-Spuren V3/V4 sind standardmäßig ausgeblendet. Fixes: (a) **Whisper-Rettungspass** (`ingest.py`):
bei VAD-Abdeckung < max(10 s, 15 % der Dauer) Volllauf über die ganze Datei; übernommen werden nur Segmente ohne
Pass-1-Overlap, ohne Halluzinations-Treffer und mit mittlerer Wort-Konfidenz ≥ 0,35 (fail-closed). 19 Clips
re-transkribiert (Szene 4 komplett + 5 Audit-Treffer Szene 3/5), alle mit Zugewinn (1→9, 2→10, 1→15 …).
(b) **Aktions-Beats** (`beats.py`): Szenen mit ≤ 1 Dialogzeile bekommen je Schluss-Aktion einen eigenen Beat
(letzter bleibt „schluss“ für den Schluss-Auslauf) — Szene 4: 3 → 5 Beats. (c) **Vor-Slate-Regel + Kamera-Zuruf**
(`klappe.py`): Spiel beginnt nie vor dem Ende der ersten Klappen-Ansage; kurze „Kamera …“-Zurufe („Kamera, Lois.“ =
Whisper-Fehlhörung von „Kamera läuft!“) sind Produktion — vorher vergiftete die Fehlklassifikation `erster_spiel`
und kippte sogar das Regie-„Bitte.“ ins Spiel. Nebeneffekt: `S004_S004_T002` (Dateiname falsch beschriftet, WAV
`SZENE5-2-1-001`) wird per Inhalts-Passung korrekt Szene 5 zugeordnet. Feinschnitt v19: 4.1 T3 trägt B0
(8,8–20,8, Bild-Beleg Schreiben/Gitarre) UND B1 (35,1–41,9, „Scheiße, Mann!“); Szene-5-Finale artikuliert sich in
drei Segmente (5.2.1 T5 → 5.2.3 T1 „Es ist alles meine Schuld“ [dedizierte Einstellung, vormals transkript-leer]
→ 5.2 T4) statt eines 73-s-Laufs — gewollte Folge der wiedergefundenen Sprache. B2/B3 (Mülleimer/TV) bleiben
ehrlich als Lücke dokumentiert („ohne Bild-Beleg in einem Take“); VQA-Nachverdichtung wäre der nächste Hebel.
Sentinels unverändert grün (Sz2 15,1–49,9 · Lament 92,3–144,5 · Sz3 80,9–119,3 · 47,4–59,2 · Sz5 12,8–105,9).

**Runde-20-Nachtrag (gleicher Tag): gewichtete Szenen-Blenden + Overlap-Fehlalarm.** (a) Szenen-Übergänge
tragen ungleiches Gewicht: endet die Szene mit einem **Schluss-Auslauf** (stille Handlung nach der letzten
Zeile — Lamento, Abgang), bekommt das letzte Master-Segment `fade_schwer_s` (Default 1,0 s) statt `fade_s`
(0,4 s) als Ausblende — deterministischer Proxy über den Auslauf-Beleg, kein LLM-Urteil. Eingangsblende bleibt
0,4 s; Schnitte INNERHALB einer Szene bleiben hart. (b) Der gemeldete `normalize()`-Overlap („Track v0:
plan-…-16 overlaps plan-…-15“) war **kein Datenfehler**: Plan/Timeline sind exakt stoßfrei (306,8400 s); die
Fehler kamen aus einem **stale Next-Dev-Server**, der einen Build von VOR den Multi-Track-State-Fixes
(funktionales `numVideoTracks`-Wachstum) servierte — dort quetschte `clampIdx` die V3/V4-Alternativen
transient auf eine Spur (zwei Alternativen desselben Beats teilen denselben Start → Schein-Überlappung).
Dev-Server-Neustart → 0 Fehler. Nebenprodukt: der Editor-Guard loggt jetzt bei Invariante-Verletzung eine
strukturierte Diagnose (vIdx, numVideoTracks, überlappendes Paar in Sekunden) statt nur der Frames-Meldung.

**Einundzwanzigste Runde (2026-08-20): Rough Master v2 — „V1 erzählt, die Layer variieren“ (Nutzer-Logik).**
Grundsatz-Pivot: V1 ist KEIN Beat-Mosaik mehr, sondern der narrative Faden — **ein Take pro Szene**, der von
der Anspiel-Barriere bis zum Spielende durchläuft (keine internen Schnitte, keine Take-Wiederholung, Klappe
raus); die Beat-/Coverage-Intelligenz wandert komplett auf die Spuren darüber. Neuer `modus="master"`
(`_plane_szene_master`): Take-Wahl per Score (Beat-Abdeckung anker-gewichtet + Spielfenster-Vollständigkeit +
Bewegungs-Dynamik + Framing-Bonus); deckt kein Take die ganze Szene (getrennt gedrehte Teile), ergänzt ein
Greedy-Set-Cover die **minimale Kette** mit zwei Wächtern: (a) **Innen-Regel** — ein Beat zwischen min/max der
belegten Beats eines Takes gilt als gedeckt (Evidenz-Loch ≠ Story-Loch; sonst erzählte Szene 2 die halbe
Geschichte doppelt), (b) **sequenzielle Verantwortung** nach Story-Sort — ein Folge-Glied steigt am ersten
Beat ein, den die vorigen Glieder NICHT erzählt haben (sonst wiederholte 4.3 T5 die Replik von 4.1 T3).
`alternativen_fuer_plan` unverändert wiederverwendet (richtet sich an den takt-Spans des Master-Takes aus);
fehlt dem Master ein Span für einen innen gedeckten Beat, wird am Ende des letzten vorherigen Spans
interpoliert statt an den Segment-Anfang gestapelt. Cutaways in V1 aus (Coverage lebt auf V3+), gewichtete
Szenen-Blenden greifen über den neuen „Nachklang:“-Beleg (≥ 4 s stille Handlung nach der letzten Zeile).
**Rough Master v20** (Plan `d3fc9a5f`, Timeline `5124d36a`): V1 = Sz2 2.1 T2 (187 s) · Sz3 3.1 T4 (176 s) ·
Sz4 4.1 T3 → 4.3 T5 (Kette, Einstieg an B2/Mülleimer) · Sz5 5.1.1 T3 (146 s); 26 Coverage-Segmente auf V3/V4,
je Beat genau EINE Ausrichtung. Neue Master-Sentinels: 1 Take/Szene (Kette nur bei getrennten Teilen), kein
Take doppelt je Szene, kein Segment-Start vor Slate-Ende, V1 überlappungsfrei. Alt-Modus „feinschnitt“
(Mosaik) bleibt als Parameter erhalten.

**Runde-21-Nachtrag: Teil-Regel + Ketten-Atem (Nutzer-Befund v20 „der Film endet vor dem Schluss“).**
Der 5.1-Master beanspruchte die 5.2-Konfrontation über ein fernes 17-s-Echo (weiche Anker 0,66/0,59) —
V1 endete bei ~10:48 ohne B12/B13. Drei Regeln: (a) Ketten-Anspruch auf Dialog-Beats nur mit HARTEM Anker
(≥ 0,62); (b) **Teil-Regel** bei dreistufiger Klappe: jeder Beat hat einen Heimat-Teil (Teil des Takes mit
der stärksten harten Evidenz, Beats ohne Anker erben), ein Take beansprucht nur Beats seines Teils;
(c) **Ketten-Atem**: nur das LETZTE Glied läuft bis zum Spielende, frühere enden nach ihrem letzten
Anspruchs-Beat + 3 s. Rough Master v21 (Plan `e8dec2b3`, Timeline `eec8affd`): Szene 5 = 5.1.1 T4 (B0–B8,
inkl. Freds Abgang) → 5.2.1 T5 (Konfrontation B10–B11) → 5.2.3 T1 (B12 + B13-Schluss, läuft aus, Blende
1,0 s). Einziges ehrliches Loch: B9 („Ok“, nirgends hart belegt). 711 s, V1 überlappungsfrei, Konsole sauber.

**Zweiundzwanzigste Runde (2026-08-20): Ruf-Serien bleiben Spiel + Atem ab letzter Äußerung (Nutzer-Befund
v21, Szene 4: „4.1 T3 wurde geschnitten, obwohl die Handlung weiterlief — sie steht auf, geht zum Fernseher,
beschwert sich“).** Zwei Wurzeln: (a) Die Ausstieg-Erkennung (`klappe.py`) nahm die gespielte Ruf-Serie
Richtung TV („Hallo?“ ×2, „Hey, Samma?“ ×2, 70–90 s, ohne Skript-Treffer) für einen Ausstieg → alles ab 71 s
wurde Produktion, spiel_ende schrumpfte auf 52,7 s. Neue Regel: kurze Zurufe (≤ 4 Wörter, kein
Ausstieg-Lexikon), die sich binnen 25 s wiederholen, sind Performance — ein echter Ausstieg ist EIN Satz,
gefolgt von Crew/Stille. T3 spiel_ende 52,7 → 90,0; B1-Span 35–95. (b) Der Ketten-Atem (+3 s) zählte ab dem
letzten ANSPRUCHS-Beat statt ab der letzten GESPIELTEN Äußerung — Nicht-letzte Glieder enden jetzt bei
max(Anspruchs-Ende, letzte Spiel-Äußerung) + 3 s. Rough Master v22 (Plan `b85a7ff5`, Timeline `54211e18`):
Szene-4-Glied 1 = 4.1 T4 24,1–115,0 (91 s — Aufstehen, Gang zum TV, Beschwerde inklusive; T4 schlägt T3 im
Score knapp per Anker 0,81, T3 bleibt als Coverage vergleichbar), dann 4.3 T5 ab Mülleimer. 746 s gesamt,
V1 überlappungsfrei, Konsole sauber.

**Dreiundzwanzigste Runde (2026-08-20): Story-Repeat-Wächter an der Ketten-Naht (Nutzer-Befund v22, ~8:13:
„der Ton passt nicht zur gespielten Sequenz“).** Diagnose-Irrweg dokumentiert: Verdacht fiel erst auf
Audio-Link (Timecode-Matching der 4.3-Serie ist korrekt — der Namens-Versatz T00N↔WAV-00(N+1) kommt von
einem Fehlstart-File 12:09, 48 min vor dem ersten Take) und auf einen falsch gebauten Proxy (Fehlgriff:
per hartkodiertem `find` den Proxy des NAMENSGLEICHEN Scene-5-Takes `PPRM23_S005_S002_T005` inspiziert —
beide heißen „T005“; der echte Proxy `1cad3599` ist bild- und tonrichtig). Wahre Ursache: an der
Ketten-Naht Szene 4 stieg Glied 2 (4.3 T5) bei 33,2 s ein — MITTEN in seiner eigenen Version der
Replik („Ey, du Scheißteil! Funktionier!“ 32,6–36,3), die Glied 1 (4.1 T4) gerade zu Ende erzählt hatte:
die Story rembobiniert, die Zeile läuft doppelt, während das Bild schon weiter ist. Fix in
`_plane_szene_master`: Einstieg eines Folge-Glieds ≥ Ende der EIGENEN Spans aller bereits erzählten
Dialog-Beats (+0,3 s) — die verbleibende unaligned Fortsetzung („Was zur Hölle geht an“) liest sich als
Raccord (dieselbe Beschwerde im neuen Winkel). Rough Master v23 (Plan `4946f1cb`, Timeline `87297cac`):
Sz4-Naht 36,6 s, Sz5-Nähte 17,3/54,5 s. Lessons: bei Datei-Detektiv-Arbeit NIE Ids aus Timeline-Labels
hartkodieren — immer über `Clip.dateiname → id` auflösen (zwei Takes können „T005“ heißen).

**Vierundzwanzigste Runde (2026-08-20): Leerbild-Trim mit Inhalts-Deckel (Nutzer-Befund v23, Sz2→Sz3-Naht:
„der Ton ist richtig, aber die Szene selbst nicht“).** Medienschicht geprüft und sauber (Proxy = Original,
Dauer exakt); Ursache dramaturgisch: der Szenen-Master 3.1 T4 stieg an der Anspiel-Barriere (19,3 s) ein —
8 s LEERER Raum nach der Szenen-Blende (das Betreten kommt erst ~27 s), was wie eine falsche Szene wirkt.
Neue Regel für das ERSTE Ketten-Glied: beginnt der Take nach der Klappe mit leerem Bild, startet die Szene
bei max(Barriere, min(Präsenz − 3 s, erster Inhalt − 1 s)) — Präsenz = frühestes Gesicht (TakeKontext.
gesichter-Spans) ODER Bewegungs-Einsatz (AK.aktives_fenster); „erster Inhalt“ = Beginn des ersten
Anspruchs-Beat-Spans bzw. der ersten gespielten Äußerung. Der Deckel ist entscheidend: Szene 5 öffnet mit
OFF-SCREEN-Dialog hinter der Tür („Orpheus, bist du da?“ bei 22 s, erstes Gesicht erst 50 s) — ohne Deckel
hätte der Trim die halbe Eröffnung gefressen; Szene 2 bleibt unangetastet (gespielter Inhalt — schlafende
Yuri — ab Take-Beginn, Regel aus Runde 6). Rough Master v24 (Plan `3cca121c`, Timeline `6c3d0a9d`):
Sz3-Einstieg 25,8 s (1 s vor dem Betreten). 735 s, Konsole sauber.

**Fünfundzwanzigste Runde (2026-08-20): Drehtag-Kollision im Sync — „Bilder der Rückkehr, Ton der
Gitarren-Szene“ (Nutzer-Befund, kritisch).** Kern: `PPRM23_S003_S001_T001` (Video: Szene 3, Ophelia+Fred
kehren vom Konzert zurück) trug per Timecode-Match (0,99!) die WAVs `SZENE4-004/005` — dessen Audio-Klappe
sagt „Scene 4.1, Take 4“, also übernahm der Kontext die falsche Szene, und der Szenen-Master zeigte
Szene-3-BILDER mit Szene-4-TON. Wurzeln: (a) Videos hatten kein Drehdatum (LTC = nur Uhrzeit) → der
Same-Day-Guard im Matcher griff nie; (b) der Tonrekorder wurde an Tag 2 auf 00:00 resettet → Tag-2-Videos
(Uhrzeit ~11:45) kollidierten numerisch mit Tag-1-WAVs. Fixes: (1) Video-Drehtag aus Container-
`creation_time` (probe.py, + DB-Backfill) — aber als **Session-Cluster** (Lücke > 6 h = neuer Tag,
Label = Session-Beginn), weil die Kamera-Uhr abends über ihre Mitternacht rollt (Szene 5, LTC 19:21 =
creation 01:39!); (2) `matcher.drehtag_rang`: Tage werden über **Szenen-Affinität** der Dateinamen gepaart
(Kamera-Tag {S004,S005} ↔ Ton-Tag {SZENE4,SZENE5}), nie über absolute Datums-Gleichheit — Kamera- und
Rekorder-Uhren tragen verschiedene absolute Daten; ungepaarte Tage matchen nie per TC. Re-Match:
6 echte Szene-5-Links identisch restauriert (+Rettungspass: 17–29 Segmente), der Frankenstein-Link weg;
Transkript/Proxy/VQA des Takes gepurgt, neu gebaut, per Dateiname korrekt als 3.1 T1 eingeordnet.
**Rough Master v25** (Plan `e8600c7e`, Timeline `c149e2d0`): V1 = Sz2 2.1 T2 → Sz3 3.1 T4 (echte Datei) →
**Sz4 4.1 T3 8,8–97,9 s (Gitarre→Wut→Aufstehen→TV — der Take, den der Nutzer von Anfang an vermisste)** →
4.3 T5 (Geist) → Sz5 5.1.1 T1 → 5.2.1 T5 → 5.2.3 T1 (Schluss). Story-Reihenfolge = Skript. Konsole sauber.

**Sechsundzwanzigste Runde (2026-08-20): Geschwister-Spuren — „das System soll VERSTEHEN, dass 4.1 und
4.3 dieselbe Szene sind“ (Nutzer).** Erkenntnis: das Signal steckt in der Klappen-Nummerierung selbst —
zweistufig (4.1/4.3) = dieselbe Handlung aus anderen Winkeln (Coverage-GESCHWISTER), dreistufig
(5.1.x/5.2.x) = getrennte Szenen-TEILE. Der Mosaik-Code wusste das (`teil()`), die Master-Kette nicht.
Drei Änderungen: (1) Kette nur noch ÜBER Teil-Grenzen (ein Take pro Teil); zweistufige Szenen = genau
EIN V1-Take (Szene 4: 4.1 T3 allein, 8,8–111,1 s — Gitarre→Wut→TV→Geist in einem Fluss). (2) NEU
`geschwister_fuer_plan`: je Szene die besten Takes der Nicht-Master-Einstellungen als GANZE stumme
Segmente auf **V2/V3**, am gemeinsamen harten Anker-Beat synchronisiert (die Replik fällt auf denselben
Timeline-Moment — Spur einblenden = Szene im anderen Winkel, quasi-synchron; ohne gemeinsamen Anker:
an der Szenen-Öffnung). (3) Kurz-Extrakte je Beat rutschen auf **V4/V5** und überspringen Einstellungen,
die schon als Geschwister liegen. Feste Spur-Semantik: V1 Master · V2/V3 Geschwister · V4/V5 Extrakte
(MAX_TRACKS 5 exakt ausgeschöpft). Timeline-Labels ohne Dateinamen („Sz4 4.1 T3“, Nutzerwunsch).
Rough Master v26 (Plan `11887317`, Timeline `a1c7fcd2`): 43 Segmente (V1 12 · V2 4 · V3 3 · V4 21 · V5 3),
alle Spuren überlappungsfrei, Konsole sauber. Szene 5: Glied 1 = 5.1.1 T1 (deckt jetzt B0–B9), Glied 2 =
5.2.1 T5 bis Spielende 214 s (Knie-Ende in der Traîne); 5.2.3-Schluss als Geschwister auf V2 vergleichbar.

**Siebenundzwanzigste Runde (2026-08-20): Moment-Erkennung der Geschwister-Spuren (Nutzer: „bon endroit,
mais pas au bon moment“).** Die V2/V3-Geschwister hingen an EINEM Anker (bzw. der Szenen-Öffnung) — jetzt
richtet ein **Median-Konsens über alle gemeinsamen Momente** aus, Signal-Kaskade: gemeinsame Skript-Zeilen
(Score ≥ 0,6; dichtestes Signal) → gemeinsame harte Anker-Beats → gemeinsame VQA-Bild-Aktionen (stumme
Szenen) → **interpolierte Beat-Position** (das Geschwister trägt einen Beat, den der Master nicht belegt —
5.2-T4s B12 landet nach dem B11-Ende des Masters statt an der Szenen-Öffnung) → Szenen-Öffnung als letzter
Ausweg. Robustheit: größter Offset-Cluster (±4 s) statt rohem Median (ähnliche Lament-Repliken erzeugten
±47 s Streuung); sind alle Kandidaten isoliert, gewinnt das ERSTE gemeinsame Signal (Szenen-Einstieg =
verlässlichster Moment). Beleg je Geschwister: „Moment-Sync: Median aus N …, Streuung ±x s“ — die Streuung
ist das ehrliche Qualitätsmaß der Synchronität. Rough Master v27 (Plan `0f7b74b4`, Timeline `283f84f5`),
Spuren V1 12 · V2 4 · V3 3 · V4 21 · V5 3, überlappungsfrei, Konsole sauber.

**Achtundzwanzigste Runde (2026-08-20): ELASTISCHE Geschwister — Beat-für-Beat-Sync (Nutzer: „ce n'est pas
souvent les mêmes paroles… comment les poser bien?“).** Erkenntnis: ein globaler Offset KANN nie stimmen —
zwei Takes haben nie dasselbe Tempo und selten denselben Wortlaut (Impro). Multicam-Logik stattdessen:
das Geschwister wird an JEDEM Beat neu synchronisiert. `geschwister_fuer_plan` v2: (a) Master-Karte
Beat → Timeline-Moment (belegte Spans direkt, Lücken am Ende des letzten vorherigen Spans interpoliert,
+2 Beats Nachzügler-Fenster); (b) Sync-Punkte = evidenzierte takt-Spans des Geschwisters ∩ Master-Karte,
streng monoton in beiden Achsen; (c) je Sync-Punkt EIN Block bis zum nächsten Sync-Punkt, gerognet wenn
das Geschwister langsamer spielt (min aus Geschwister-Restzeit und Master-Fenster), 3 s Vorlauf am ersten
Block, Blöcke < 1,5 s entfallen; kein gemeinsamer Moment → ehrlich weggelassen. Drift damit auf EINE
Beat-Länge begrenzt. Beispiele v28: 2.2 T4 = 3 Blöcke (B2 Aufwachen / B3 / B4 Lament) je am
Master-Moment; 5.2.3 T1 = B11 „Es ist alles meine Schuld“ + B12 „Bitte, komm wieder“ exakt auf den
Master-Beats. Rough Master v28 (Plan `6da8ec08`, Timeline `7effec56`), V1 12 · V2 8 · V3 3 · V4 21 · V5 3,
überlappungsfrei, Konsole sauber.

**Neunundzwanzigste Runde (2026-08-20): Klappen-Schutz auf den Overlay-Spuren (Nutzer: „je vois le clap
dans la V2“).** Der sichtbare-Klappe-Trim (`anfang_nach_klappe`, Bewegungs-Heuristik) lief nur auf
Dialog-Master-Segmenten — Geschwister-Blöcke, Extrakte, stumme Master und Inserts starteten teils in der
Klappe. Fixes: (a) NEU `_klappe_sichtbar_bis` — CLIP-Zweitsignal („clapperboard vor der Kamera“ vs. „Raum
ohne Equipment“, Frames teilen den VQA-Cache), nötig weil die Bewegungs-Heuristik auf statischen Clips
blind ist; in `_dialog_segment_bereinigen` (nur_anfang) als zweite Stufe hinter der Bewegungs-Heuristik.
(b) Anwendung auf ALLE Startbereiche: Dialog- UND Stumm-Master, Inserts, und eine Overlay-Nachpass für
Geschwister/Extrakte (in_s < 20 s) — dort wandert `tl_start` um das Trim-Delta mit, damit der
Beat-Sync erhalten bleibt; Blöcke < 1,5 s entfallen. Befund-Beispiel: Geschwister 2.4 T1 startete bei
3,7 s (Klappe bis ~8 s im Bild) → jetzt 8,3 s, Sync kompensiert. Rough Master v29 (Plan `d02b25ea`,
Timeline `c15a800f`), Spuren unverändert (V1 12 · V2 8 · V3 3 · V4 21 · V5 3), überlappungsfrei,
Konsole sauber. V1 war bereits klappen-frei (Nutzer-Bestätigung; Szene-1-Inserts zeigen Blumen-Detail,
keine Klappe — CLIP-Check dort korrekt still).

**Dreißigste Runde (2026-08-20): Spur-Regeln fürs Verschieben (Nutzer: „V2+ frei bewegen, ohne dass die
anderen folgen“).** Zwei Editor-Bugs im Gruppen-Drag: (a) ein Drag, der auf einem OVERLAY-Clip (V2+)
begann, zog die gesamte Mehrfach-Auswahl mit (inkl. unsichtbar per Rubber-Band mitselektierter
V1-Master); (b) der t=0-Clamp wirkte PRO Clip statt uniform — beim Ziehen nach links stoppte der
Sz2-Master bei 0, der Sz3-Master schob weiter → v0-Überlappung, Toast-Sturm „Timeline-Modell ungültig“.
Fixes in Editor.tsx: Overlay-Drags (startTrackIdx ≥ 1) sind immer SOLO (weder visuelle Gruppen-Translation
noch Gruppen-Commit — Coverage verschieben darf nie andere Clips bewegen); Gruppen-Drag bleibt V1-Sache
mit UNIFORMEM Delta (geklemmt am frühesten Gruppen-Clip) + Kollisionscheck auch INNERHALB der Gruppe.
tsc 0 Fehler (außerhalb _archive), v29-Timeline neu geladen, Konsole sauber.

**Einunddreißigste Runde (2026-08-20/21): „Senden an DaVinci“ — Timeline ODER ganzes Projekt.**
Befund: der bisherige Export scheiterte hart („bad any cast“) — OpenTimelineIO ist unter Python 3.14
kaputt (scheitert am eigenen Plugin-Manifest). Ersatz: EIGENER FCPXML-1.8-Generator
(`backend/core/fcpxml_export.py`), der kann, was OTIO nie konnte: absolute Positionen mit Gaps im Spine,
V2–V5 als Connected Clips (Lanes 1–4), Overlay-Clips `enabled="0"` (stumme Coverage bleibt aus),
pro V1-Clip der ALIGNIERTE WAV als Audio-Lane (−1; wav_zeit = video_zeit − sync_offset), Beat-Marker +
Szene/Einstellung/Take-Notiz je Clip. Server-Anreicherung `_bereichere_segmente` (export.py) zieht alles
aus der DB. Zwei Sende-Modi (`mode`): **timeline** = FCPXML wie bisher (Direktimport via Scripting,
Fallback Finder); **projekt** = zusätzlich `<name>.projekt.json`-Manifest → erweitertes
`davinci_import.baue_projekt`: Bins je Szene (Szene 1–5 + Audio) mit den Originalen, Clip-Metadaten
Scene/Shot/Take, Beat-Marker (blau) auf den Media-Pool-Items, danach Timeline-Import. Frontend: Senden-Menü
mit DaVinci-Untermenü („Nur Timeline senden“ / „Ganzes Projekt aufbauen“), fps jetzt korrekt PROJECT_FPS
statt 30. Verifiziert headless: FCPXML der v29-Timeline valide (52 Clips, 35 deaktivierte Overlays,
59 Beat-Marker, Lanes −1…4, 6 Bins im Manifest); der Resolve-Scripting-Teil braucht eine laufende
Resolve-Instanz mit External Scripting = Local (Erst-Test durch den Nutzer per Klick).

**Runde-31-Nachtrag (21.08., nachts): Resolve-Feinschliff nach dem ersten Live-Test.** Nutzer-Test ergab:
Projekt + Szenen-Bins entstehen ✅, aber (a) `ImportTimelineFromFile` gab None → Fallback Finder, (b)
Drag-and-Drop aus dem Bin bringt den Ton nicht mit. Fixes: (1) **EXPORT_DIR von ~/Documents nach
~/Movies** — Documents ist iCloud-synchronisiert und der iCloud-Speicher des Nutzers ist VOLL (alle
Export-Dateien trugen „Fehler“-Badges; evakuierte/dataless Dateien kann Resolve nicht lesen) — Haupt-
verdächtiger für den None-Import. (2) **AutoSyncAudio** in `baue_projekt`: vor dem Timeline-Import
bekommen die Tag-2-WAVs (Rekorder-Reset auf 00:00) ihren korrigierten Start-TC aus dem Manifest
(`audio_tc` = video_tc + sync_offset, von `_bereichere_segmente` berechnet), dann verbindet
`MediaPool.AutoSyncAudio` (timecodeAccuracy) Video-Items mit ihren WAVs zu Sync-Clips → Drag-and-Drop
trägt Bild UND Ton. Live-Bisect gegen Resolve war nicht abschließbar (Scripting-Session verklemmte nach
Prozess-Kill; Resolve-Scripting ist single-client) — nächster „Ganzes Projekt“-Klick des Nutzers ist der
Test; schlägt der Timeline-Import weiter fehl, liegt es an der FCPXML-Struktur (Kandidaten: Kind-Reihenfolge
note/marker, lane=-1-Audio, enabled=0) und wird per Feature-Bisect isoliert.

**Runde-31-Abschluss (21.08., 01:15): Resolve-Handoff FUNKTIONIERT — Timeline per API statt FCPXML.**
Live-Bisect ergab: Resolve 20 lehnt via `ImportTimelineFromFile` JEDE FCPXML ab (auch handgeschriebene
Minimalreferenz, alle Versionen/Options-Varianten) — der Scripting-FCPXML-Import ist dort faktisch tot.
Lösung: die Timeline wird DIREKT über die API gebaut (`CreateEmptyTimeline` + `AddTrack` +
`AppendToTimeline` mit startFrame/endFrame/trackIndex/recordFrame/mediaType) — das Manifest trägt jetzt
auch die Timeline-Segmente; Video-Items als mediaType=1 (Kamera-Ton ist stumm), alignierte WAVs als
mediaType=2 auf A1, Overlays nach dem Einfügen `SetClipEnabled(False)`. E2E-verifiziert gegen laufendes
Resolve: Projekt `CinAssist 20260821_011145` — Bins Szene 1–5 + Audio, Timeline 24 fps mit V1 12 aktiv /
V2–V5 35 deaktiviert / A1 5 WAVs, Metadaten Scene/Shot/Take gesetzt (Stichprobe 2.4 T1 ✓). Die FCPXML
bleibt als Artefakt für Premiere/FCP/manuellen Import. Merke: Resolve-Scripting ist SINGLE-CLIENT —
ein gekillter Script-Prozess verklemmt die Session bis zum Resolve-Neustart.
