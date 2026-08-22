# Sync-Modell — Methodik und Ergebnisse (Stand 2026-08-18)

Begleitdokument zu `README.md` (Architektur/Kaskade). Hier: **wie** vorgegangen wurde,
**was** auf dem Referenz-Korpus herauskam, und **wo** die Daten dem Auftrag widersprechen.

## 1. Methodik

1. **Étape 0 zuerst, kein Code davor.** Referenz-Decoder aus dem Auftrag unverändert auf
   `PPRM23_S004_S003_T001.MOV` Kanal 3 → `12:57:04:07` ✓. Danach auf T002–T006: **0 Frames** auf
   T002/T003/T004/T006. Ursache: `T = median(iv[iv > percentile(iv, 60)])` — das 60. Perzentil
   liegt exakt auf der Bitlänge, Maske leer, `NaN`. Auf Rausch-Kanälen lieferte das Sync-Wort
   außerdem Zufallstreffer (`41:33:71:36`). Beides behoben (robuste Periode, BCD-Range,
   Kontinuität, sample-genauer Start-TC), Regressionstests dafür.
2. **Reine Logik vor I/O.** Parser (`bwf_ixml`), Decoder (`ltc`), Korrelation (`waveform`),
   Namen (`namen`) und die Kaskade (`matcher`) arbeiten auf Dataclasses/NumPy — ohne DB, ohne
   ffmpeg-Aufruf im Matcher (Wellenform/Klappe sind injizierbare Funktionen). Erst danach
   `probe.py` (Scan/ffprobe), Worker, API, UI.
3. **Korpus als Prüfstand, nicht als Erwartungsquelle.** Jede Behauptung aus dem Auftrag wurde
   an den Dateien nachgemessen (Chunk-Dump der WAVs, LTC aller 58 MOVs, Container-Tags,
   Resource-Forks). Wo Tabelle und Daten auseinanderliegen, wurden die **Daten** codiert und der
   Widerspruch dokumentiert (Abschnitt 3).
4. **Fail-honest statt fail-silent.** Nicht anwendbare Stufen (Wellenform ohne Scratch, Klappe
   ohne Video-Ton, Container-TC-Artefakt) werden als Text im Take/Link gemeldet.
5. **Ende-zu-Ende-Beweis.** Import → Matching → A/B-Vorschau → „Analyse starten“ → Whisper auf
   dem verknüpften WAV, geprüft über die echte API + Celery + UI (nicht nur Unit-Tests).

## 2. Ergebnisse auf dem Korpus (`/Volumes/DSCVR/DOKUMENTEN/SHORTCUT 24`)

| Schritt | Ergebnis |
|---|---|
| Video-Import `ROHMAT_VIDEO` | 58 Dateien, **58 `._*` ignoriert**, 17 s; LTC Kanal 3 auf 55/58 (3 ohne TC: `S001_S001_T011/T012/T014`), Container-TC `16:46:20:04` auf allen 58 → verworfen |
| Audio-Import `ROHMAT_AUDIO/11-17-23` | 30 WAV, 30 `._*` ignoriert, 0,6 s; alle mit bext-TC, Spur „Record“ = Kanal 0 |
| Re-Import | 0 neu / 30 aktualisiert — keine Duplikate (Fingerprint) |
| Matching (58 × 30) | **27 sicher · 0 plausibel · 2 unklar · 33 verwaist**, zweimal hintereinander byte-identisch |
| Szene 4 / Einstellung 3 | T001↔`+SZENE4-3-002` −2,700 s · T002↔`-003` −3,117 s · T003↔`-004` −3,077 s · T004↔`-005` −2,243 s · T005↔`SZENE4-3-006` −2,702 s — alle `sicher`, `timecode`, Konfidenz ≥ 0,986, Warnung „Take-Nummern verschoben (+1)“ auf allen 5, `+` auf allen 4 markierten gemeldet |
| A/B-Player | Drift 2–3 ms in Lecture; Pause → Audio stoppt; Seek 40 s → Audio 42,7 s |
| Analyse T001 | Whisper auf `+SZENE4-3-002.WAV` Kanal 0, Offset −2,7 s → *„It's going. Scene 4.3, take one … Hallo? Oh, du Scheißteil, Mann! …“* (vorher: „Thank you.“); Proxy trägt den verknüpften Ton (−38 dB RMS vs. −97 dB Kameraspur) |
| Tests | 42 grün (`backend/tests`), 2 s ohne DB / 6 s mit DB |

## 3. Widersprüche Auftrag ↔ Daten (bewusst so codiert)

- **`+SZENE4-3-001` ist kein Waisen-Ton.** bext 12:09:21,8 (42,9 s) liegt vollständig in
  `PPRM23_S004_S002_T002.MOV` (LTC 12:09:24,0, 38,8 s) → `sicher`, Offset −2,24 s, Warnung
  „Einstellungs-Nummern widersprechen sich (Audio 3 ↔ Video 2)“. Die Tabelle des Auftrags
  kannte nur Einstellung 3.
- **`T006` ist kein Waisen-Bild.** `SZENE4-4-001.WAV` (13:30:23,3, 40,6 s) deckt es zu 100 %
  → `sicher`, Offset −2,29 s (Warnungen: Take 001 ↔ T006, Einstellung 4 ↔ 3).
- **„Ein Audio überlappt zwei Videos → unklar“ verfeinert.** `SZENE4-002.WAV` (163 s) deckt
  `S004_S001_T001` zu 90 % und `T002` zu 100 % — der Ton lief einfach durch: beide `sicher`, je
  eigener Offset (+1,95 s / −63,84 s). `unklar` nur, wenn die betroffenen Videos sich **selbst**
  zeitlich überlappen: `S003_S001_T001 ∥ S004_S001_T004` (77 s parallel, `SZENE4-005.WAV`
  deckt beide) → 2 × `unklar` mit Kandidaten. Randüberlappungen (< 80 %) blockieren nichts mehr.
- **Klappe (Stufe 3):** `_looks_like_klappe()` braucht Szenenbeschreibungen, die bei der
  Ingestion noch nicht existieren; ohne Kamera-Scratch gibt es keinen Offset → injizierbar,
  standardmäßig inaktiv.

## 4. Nebenbefunde

- iXML `HISTORY/ORIGINAL_FILENAME` zeigt, dass die WAVs **am Gerät umbenannt** wurden
  (z. B. `SZENE4-3-008 → +SZENE4-3-002`) — als Warnung am Asset sichtbar.
- Die App nutzt den **nativen Homebrew-Postgres** auf 127.0.0.1:5432, nicht den
  docker-compose-Container. Test-DB: `cinassist_test`.
- `CINASSIST_DATA_DIR` war in der laufenden Instanz nicht gesetzt → Vorschau-Derivate landeten
  unter `backend/proxies/sync/` (interne Platte). Für den Auftrag auf das SanDisk zeigen lassen.
