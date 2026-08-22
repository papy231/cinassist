# Audio/Video-Synchronisation bei der Ingestion (Take-Modell)

Zwei Ordner — Kamera-Videos und separat aufgezeichnete WAVs — werden **per Referenz**
importiert (kein Kopieren, kein Reencodieren der Originale). Vor jeder Analyse ordnet
CinAssist jedem Ton sein Video zu, mit **Offset, Konfidenz und Begründung**, zeigt das zur
Validierung an und lässt Whisper/Diarization erst danach laufen — **auf dem verknüpften WAV**,
nicht auf der Kameraspur (die auf dem Referenz-Korpus nur LTC + Stille enthält).

Deterministisch, kein LLM: gleiche Dateien → gleiche Verknüpfungen.

## Module

| Datei | Aufgabe | Testbar isoliert |
|---|---|---|
| `bwf_ixml.py` | RIFF-Chunk-Parser: `fmt`/`bext`/`iXML`/`data` (auch RF64). Timecode = `bext.time_reference / sample_rate`, gegen `iXML.TIMESTAMP_SAMPLES_SINCE_MIDNIGHT` geprüft (Divergenz → `tc_quelle = keine`). Spurnamen (`Record`, …), `SCENE`/`TAKE`/`TAPE`/`CIRCLED`, `TIMECODE_RATE`/`_FLAG`. | `test_bwf_ixml.py` |
| `ltc.py` | LTC-Decoder (SMPTE 12M Biphase-Mark) + Kanal-Erkennung (Energie / Nulldurchgangsrate / Bimodalität). Start-TC **sample-genau** aus erstem gültigen Frame. Range-Check der BCD-Werte, Kontinuitätsprüfung (≥ 90 % monotone +1-Frames), fps aus Bitrate (24/25/30). | `test_ltc.py` (synthetischer Encoder + Korpus T001–T006) |
| `waveform.py` | Stufe 2: FFT-Kreuzkorrelation 8 kHz mono, Peak ≥ 3× über bestem Peak außerhalb ±0,5 s. Meldet **„nicht anwendbar“**, wenn der Video-Kanal stumm ist. | Sign-Konvention geprüft (`test_matcher.py` per Stub) |
| `namen.py` | Stufe 4: `PPRM23_S004_S003_T001` / `+SZENE4-3-002` / iXML `SCENE`+`TAKE` → (Szene, Einstellung, Take, unbekannte Markierung). Drehtag aus `TAPE` (YYMMDD), Ordner (`MM-DD-YY`) oder Dateiname. | `test_namen.py` |
| `matcher.py` | Kaskade auf Dataclasses (kein I/O): Timecode → Wellenform → Klappe → Dateiname; Waisen; Warnungen; `matche_nach_dateiname()` nur auf Wunsch. | `test_matcher.py` |
| `probe.py` | Ordner-Scan (Filter `._*`, `$RECYCLE.BIN`, `TRASH`, `UNDO`, `SETTINGS`, `.Spotlight-V100` …), `ffprobe`, LTC-/Scratch-Kanal, Fingerprint (sha256 der ersten 4 MB + Größe), Volume-UUID (`diskutil`), Container-TC-Verwerfung. | `test_probe.py` |

Persistenz/Orchestrierung: `backend/workers/sync.py` (Celery: `cinassist.import_ordner`,
`cinassist.sync_matchen`, `cinassist.sync_vorschau`) · API: `backend/api/sync.py` ·
Ingestion: `backend/workers/ingest.py` (`_verknuepfter_ton`, `_ton_ausrichten`,
`schritt_audio_extrahieren(..., ton_pfad, ton)`) · UI: `src/components/SyncPanel.tsx`.

## Datenmodell (`backend/core/database.py`)

```
ordner_importe    pfad, typ, gescannt_am, anzahl_dateien, anzahl_ignoriert, volume_uuid, volume_root, status
media_assets      typ, pfad (absolut), dateiname, dauer_s, sample_rate, kanaele, fps, codec, dateigroesse,
                  tc_start "HH:MM:SS:FF", tc_start_s, tc_quelle ∈ {bwf, ixml, ltc, container, keine}, tc_rate, tc_flag,
                  ixml_json, fingerprint (unique), ordner_import_id, ltc_kanal, scratch_kanal, record_kanal,
                  container_tc, szene, plan, prise, unbekannte_markierung, datum, warnungen
takes             video_asset_id (NULL = Ton ohne Bild), szene, plan, prise,
                  status ∈ {sicher, plausibel, unklar, verwaist, manuell_bestaetigt, manuell_abgelehnt},
                  warnungen, kandidaten_json, automatisch
take_audio_links  take_id, audio_asset_id, offset_s (= audio_start − video_start, signiert, ms),
                  methode ∈ {timecode, waveform, klappe, dateiname, manuell, verwaist}, konfidenz 0..1,
                  begruendung, kanal_fuer_transkription (Spur „Record“, sonst 0), warnungen, bestaetigt
clips.take_id     Clip = dünne Schicht über einem Take (dateipfad = Original per Referenz)
```

Migration: `init_db()` (`create_all` + `ALTER TABLE clips ADD COLUMN take_id`, `dateigroesse → BIGINT`,
`dateipfad → VARCHAR(1024)`). Kein Alembic im Projekt.

## Die Kaskade — und was jede Stufe **nicht** garantiert

**Stufe 1 — Timecode.** Audio (bext) und Video (LTC) auf derselben Time-of-Day-Uhr; gleicher
Drehtag (falls bekannt), kompatible Rate. Paar = Kandidat, wenn die Intervalle
`[tc_start, tc_start + dauer]` sich um ≥ 20 % der kürzeren Dauer oder ≥ 5 s überlappen.
*Stark* = > 80 % → `sicher` (Konfidenz 0,95–0,99). Nur schwache Kandidaten: genau einer →
`plausibel`, mehrere → `unklar`.
Ein Audio darf stark an **mehreren** Videos hängen (Ton läuft über zwei Takes durch: je eigener
Offset). Überlappen sich die Videos **selbst** zeitlich, entscheidet das Maß der Überlappung:
**≥ 50 % der kürzeren Dauer = Multicam** (zwei Kameras liefen parallel) → Ton an **beide** Takes, je
eigener Offset, Status `plausibel`, gemeinsame `multicam_gruppe` (UI-Badge MULTICAM, nicht blockierend);
**5–50 %** = Konflikt (Uhren-/Etikettierungsproblem?) → `unklar` mit Kandidatenliste; < 5 % / < 2 s =
Randüberlappung. Randüberlappungen (< 80 %) neben einem starken Treffer werden nur als Hinweis geführt,
nicht verknüpft.
→ *Garantiert nicht:* dass die Uhren wirklich synchron liefen (Jam-Sync-Drift, falsche Uhr am
Recorder), Datumszuordnung bei Videos ohne Datum im Namen (LTC ist nur Time-of-Day),
Mitternachtsübergang, Drop-Frame-Arithmetik (DF wird erkannt, aber als NDF gerechnet).

**Stufe 2 — Wellenform.** Nur wenn das Video einen **nicht-stillen, nicht-LTC** Kanal hat
(Scratch). Bestätigt Stufe 1 (|Δ| ≤ 100 ms → Konfidenz +0,02) oder widerspricht (→ `plausibel`,
Warnung). Ohne Timecode: einziger Treffer im Pool → `plausibel` (Konfidenz ≤ 0,9); der Pool wird
über Datum und — nur zum **Eingrenzen** — über Szene/Einstellung im Namen verkleinert.
→ *Garantiert nicht:* Ergebnisse ohne Scratch-Ton (auf dem Referenz-Korpus: **nicht anwendbar**,
wird so gemeldet), Robustheit bei starkem Hall/Abstand Kamera–Recorder, Sub-Frame-Genauigkeit
über 8-kHz-Auflösung hinaus.

**Stufe 3 — Klappe.** Injizierbar (`klappe_fn`); braucht ebenfalls einen Video-Ton, um einen
Offset zu bilden. `_looks_like_klappe()` (Text-Heuristik auf der Szenenbeschreibung) steht bei der
Ingestion noch nicht zur Verfügung — deshalb ist Stufe 3 in der Standard-Konfiguration nicht aktiv.
→ *Garantiert nicht:* mehr als `plausibel`, Konfidenz ≤ 0,6.

**Stufe 4 — Dateiname.** Entscheidet **nie** allein. Auf dem Korpus sind Audio-Take 002 ↔
Video T001 systematisch um eins verschoben — ein Namens-Matching wäre falsch und sähe richtig
aus. Der Name gruppiert die Anzeige (Szene/Einstellung) und erzeugt Warnungen
(„Take-Nummern verschoben“, „Einstellungs-Nummern widersprechen sich“). Auf ausdrücklichen
Wunsch: `matche_nach_dateiname()` → `methode = dateiname`, Konfidenz ≤ 0,3, `unklar`, Offset 0.

**Markierung `+`.** Kommt aus dem iXML-`SCENE`-Feld (vom Tonmeister eingegeben; `CIRCLED=FALSE`
trotzdem). Bedeutung unbekannt → `unbekannte_markierung`, wird gemeldet, **nie** als „gute Aufnahme“
interpretiert.

**Container-Timecode.** Der `timecode`-Tag des Containers wird verworfen, wenn er auf ≥ 3 Dateien
eines Imports identisch ist (Korpus: `16:46:20:04` auf allen 58 — Export-Artefakt).

**Waisen.** Audio ohne Video → `Take(video=NULL, status=verwaist)` mit `TakeAudioLink(methode=verwaist)`;
Video ohne Audio → `Take(status=verwaist)`. Beide bleiben sichtbar und manuell zuordenbar.

## Ton-Klassifikation vor Whisper (`tonklasse.py`)

Kommt ein Clip **ohne** verknüpften Ton in die Ingestion (klassischer Upload, „ohne Ton freigegeben“),
wird jeder Kanal der Quelle klassifiziert: `stille` (< −60 dBFS / < 2 % aktive Frames) · `ltc`
(dekodierbares Biphase-Signal) · `rauschen` (stationär: Pegeldynamik < 3 dB, oder < 6 dB und spektral
flach) · `nutzton`. Nur `nutzton`-Kanäle gehen in den Mono-Mix; ohne einen einzigen wird die
Transkription **übersprungen** (Job-Step `audio`: `klasse=kein_nutzton`, `transkription`: `skipped`)
statt Whisper auf Timecode-Gezirpe loszulassen („Thank you.“). Auf T001: Kanal 0–2 stille, Kanal 3 ltc
→ keine Transkription. Grenzen: Heuristik ohne ML — sehr leise, aber echte Sprache unter −60 dBFS
gilt als Stille; Musik/Atmo gilt als Nutzton (Whisper entscheidet dann selbst).

## Ingestion auf dem richtigen Ton

`Clip.take_id` → primärer `TakeAudioLink` (bestätigt > höchste Konfidenz) → `_ton_ausrichten()`:
Spur `kanal_fuer_transkription` als Mono, um `offset_s` verschoben (`atrim` bei negativem,
`adelay` bei positivem Offset), auf Video-Dauer gekürzt (48 kHz, TEMP). Daraus: Whisper-WAV
(16 kHz), Diarization, **Proxy mit gemuxtem verknüpften Ton**, Waveform-PNG. Ohne Link:
bisheriges Verhalten + Warnung „Transkription auf Kamera-Ton“ (im Job-Step `audio` und im
Pipeline-Bericht `schritt_history.sync`). `unklar` und `manuell_abgelehnt` liefern nie Ton.

## Regeln (Auftrag)

1. Originale nie kopieren/reencodieren — absolute Referenz + Fingerprint; Derivate nur unter
   `CINASSIST_DATA_DIR` (Proxies, A/B-Vorschau in `proxies/sync/`).
2. Jede Zuordnung trägt Methode, Konfidenz, Begründung.
3. Der Dateiname entscheidet nie allein.
4. `+` nicht interpretieren.
5. `unklar` blockiert „Analyse starten“ (409), außer ausdrücklich `unklar_bestaetigen=true`.
6. Kein LLM in der Sync-Kette.
7. `._*` filtern (ExFAT-Resource-Forks mit Video-Endung).

## Bedienung (UI, Tab „Synchronisation“)

1. **Ordner wählen** — „Ordner wählen …“ öffnet einen Ordner-Browser (Einstieg /Volumes, Home;
   Anzahl Video-/Audio-Dateien je Ordner) **oder Ordner aus dem Finder auf die Kachel ziehen**: der
   Browser liefert keinen Pfad, nur Name + Dateinamen → `POST /api/import/finden` sucht unterhalb von
   /Volumes und Home einen gleichnamigen Ordner mit passendem Inhalt (1 Treffer = Import, mehrere = Auswahl,
   keiner = Ordner-Browser). Wahl = Import per Referenz als Celery-Job (Fortschritt oben). Beide Ordner nötig.
2. **Synchronisieren** — Button „▶ Synchronisieren“ (Schritt 2); optional Häkchen „automatisch“ = nach dem
   zweiten Import von selbst. Ergebnis-Zeile „N sicher · M unklar · K ohne Partner“, Filter springt auf
   `unklar`, wenn es welche gibt.
3. **Prüfen & in Medien übernehmen** — Take anklicken → A/B-Vorschau wird automatisch erzeugt (Bild +
   verknüpfter Ton mit Offset). Entscheidungen: Bestätigen / Kandidat nehmen / Ohne Ton freigeben / Ablehnen /
   Abhängen / Offset ±ms. **„In Medien übernehmen“** (`POST /api/sync/in-medien`) macht aus jedem Take **ein
   Medium** im Medien-Panel — das Video mit seinem zugeordneten, synchronen Ton (Proxy trägt den Ton, Offset
   eingerechnet; Tile zeigt „♪ WAV-Name“, Sortierung „Szene/Einstellung/Take“ und „Timecode“). Dialog-Optionen:
   **Ordnung** `szene` (`Szene N/Einstellung M` direkt in der Wurzel) · `chronologisch` (`Drehtag <Datum>`) ·
   `flach`; **Ton** zusätzlich als eigenes Audio-Medium (`…/Ton`); **Waisen** Bild ohne Ton übernehmen / WAV
   ohne Bild als Audio-Medium (`…/Nur Ton`); **Analyse sofort starten**. Gesperrt, solange ein Take `unklar`
   ist — der Grund steht daneben. Die Timeline wird dabei nicht angefasst (Timeline-Aufbau = späterer Schritt).
   Antwortet das Quell-Volume nicht (I/O-Fehler, ausgeworfen), bricht der Aufruf mit 503 statt zu hängen.

## Medien-Ordner (Bins) im Medien-Panel

`medien_ordner` (Baum) + `clips.ordner_id`. Im Medien-Panel: Breadcrumb, Ordner-Kacheln (Klick = öffnen,
Rechtsklick = umbenennen/hochschieben/löschen), „Neuer Ordner“, „Ordner importieren …“ (Video-Ordner per
Referenz → Ordner mit dem Ordnernamen + ein Clip je Video + Analyse; Ton-Ordner gehören in den Tab
Synchronisation), Clips per Drag & Drop auf eine Kachel/den Breadcrumb oder per Kontextmenü „In Ordner
verschieben…“. Clips aus der Synchronisation landen automatisch in `<Importordner>/Szene N`. Die Suche
(oben) sucht ordnerübergreifend. „Alles zurücksetzen“ (Synchronisation) löscht nur den Sync-Zustand — Ordner
und Medien-Import-Clips bleiben.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Korpus-Tests (`CINASSIST_KORPUS`, Default `/Volumes/DSCVR/DOKUMENTEN/SHORTCUT 24`) und DB-Tests
(eigene DB `cinassist_test`) werden ohne Volume/Postgres übersprungen. Abnahme-Korpus „Szene 4“
in `test_import_db.py`; die 7 Fälle aus dem Auftrag (Einstellung 3) synthetisch in
`test_matcher.py`.

**Befund zum Auftrag:** Mit dem definierten Abnahme-Korpus (`PPRM23_S004_*` + `*SZENE4*`) sind
`+SZENE4-3-001` und `T006` **keine** Waisen — `+SZENE4-3-001` liegt per Timecode vollständig in
`PPRM23_S004_S002_T002.MOV` (Offset −2,24 s), `T006` wird von `SZENE4-4-001.WAV` vollständig
gedeckt (Offset −2,29 s). Die Erwartung „verwaist“ trifft nur zu, wenn man auf Einstellung 3
einschränkt (5 WAVs + 6 MOVs). Beides wird mit Warnungen zu widersprüchlichen
Einstellungs-/Take-Nummern angezeigt.
