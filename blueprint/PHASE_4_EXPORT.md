# Phase 4 — Export

> Wie aus dem Schnitt-Plan (der Timeline aus Phase 3) eine echte,
> abspielbare Videodatei wird — oder wahlweise ein Projekt für eine
> professionelle Schnittsoftware.

**Dauer:** Sekunden bis Minuten (abhängig von Anzahl und Länge der Segmente;
Timeout 10 Minuten).
**Charakter:** asynchron, in einem **Celery-Worker** (wie Phase 2), weil ein
FFmpeg-Render-Lauf zu lange für eine HTTP-Antwort dauert.
**Ergebnis:** eine fertige MP4-Datei in `backend/outputs/`.

**Quelldateien:**
[`backend/api/export.py`](../backend/api/export.py) — die Endpunkte ·
[`backend/workers/export.py`](../backend/workers/export.py) — der
Render-Worker.

---

## 1. Zielsetzung dieser Phase

Bis hierher existiert das fertige Video nur als **Plan**: Die Timeline aus
Phase 3 ist eine Liste von Verweisen *„nimm aus Clip X die Sekunden a–b"*.
Phase 4 macht aus diesem Plan ein echtes Artefakt.

Es gibt **zwei Wege**, die sich an unterschiedliche Nutzer richten:

| Weg | Endpunkt | Ergebnis | Für wen |
| --- | -------- | -------- | ------- |
| **A — Render** | `POST /api/export` | fertige MP4-Datei | Endnutzer, die ein fertiges Video wollen |
| **B — Senden an NLE** | `POST /api/export/open-in` | FCPXML-Projekt, in DaVinci/Premiere/FCP geöffnet | Profis, die selbst weiterschneiden |

Weg A ist der eigentliche „Export" und steht im Mittelpunkt dieses
Dokuments. Weg B (Abschnitt 8) ist die Brücke zu professionellen
Schnittprogrammen.

> **Warum beide Wege?** Ein KI-Schnitt ist ein **Vorschlag**, kein
> Endprodukt. Ein professioneller Cutter will den Vorschlag in seinem
> gewohnten Werkzeug feinjustieren. Weg B respektiert das: CinAssist
> liefert die Vorarbeit, der Mensch behält die Kontrolle. Weg A bedient
> alle, die sofort ein vorzeigbares Ergebnis brauchen.

---

## 2. Auslöser

Der Nutzer klickt im Editor auf „Exportieren". Das Frontend sendet die
aktuelle Timeline als `POST /api/export`. Der Request-Body wird durch das
Pydantic-Modell `ExportRequest`
([export.py:41](../backend/api/export.py#L41)) validiert:

| Feld         | Typ                   | Default       | Bedeutung                       |
| ------------ | --------------------- | ------------- | ------------------------------- |
| `segments`   | list[`SegmentExport`] | —             | die zu rendernden Segmente      |
| `resolution` | str                   | `"1920x1080"` | Zielauflösung der Ausgabedatei  |
| `name`       | str                   | `"Export"`    | Projektname                     |

Jedes `SegmentExport` trägt: `id`, `clip_id`, `track` (`v1` / `a1`),
`start` (Position in der Timeline), `dauer`, `mediaStart` (Startzeit im
Quell-Clip) und optional eine `transition` (`type` + `dauer`).

> **Wichtig:** Die Segmente kommen aus der Timeline von Phase 3 — aber sie
> können vom Nutzer **im Editor verändert** worden sein (verschoben,
> gekürzt, gelöscht). Phase 4 rendert immer den **aktuellen** Editor-Zustand,
> nicht zwingend exakt das KI-Resultat.

Der Endpunkt `export_timeline()`
([export.py:49](../backend/api/export.py#L49)) tut dann dasselbe Muster wie
Phase 1:

1. Validieren, dass überhaupt Videosegmente vorhanden sind.
2. Eine `Job`-Zeile mit `typ = "export"`, `status = "wartend"` anlegen.
3. `export_video_task.delay(job_id, {segments, resolution, name})` — die
   Render-Aufgabe in die Redis-Warteschlange legen.
4. Sofort mit der `job_id` antworten (WebSocket-Tracking wie in Phase 2).

---

## 3. Übersicht — der Render-Ablauf

```
   Timeline-Segmente  (aus Phase 3, ggf. im Editor angepasst)
        │
   ┌────┴───────────────────────────────────────────────┐
   │  export_video_task()  —  Celery-Worker              │
   └────┬───────────────────────────────────────────────┘
        │
        ├─ 1.  Videosegmente filtern + nach start sortieren     [ 5 %]
        ├─ 2.  clip_id → Dateipfad auflösen (DB)                [15 %]
        ├─ 3.  Prüfen: alle Quell-Clips vorhanden?
        ├─ 4.  FFmpeg-Befehl bauen (xfade / acrossfade)         [25 %]
        ├─ 5.  FFmpeg ausführen (subprocess, Timeout 600 s)     [35 %]
        └─ 6.  Erfolg → outputs/export_{job_id}.mp4            [100 %]
```

---

## 4. Beteiligte Werkzeuge

| Werkzeug            | Aufgabe in Phase 4                                          |
| ------------------- | ----------------------------------------------------------- |
| **Celery**          | führt `export_video_task` im Worker-Prozess aus             |
| **psycopg2-binary** | synchroner DB-Zugriff (`_resolve_clips`, `_update_job`)     |
| **redis-py**        | publiziert Fortschritt auf Kanal `job:{id}`                 |
| **FFmpeg**          | das eigentliche Rendern: schneiden, skalieren, encodieren   |
| **subprocess**      | ruft FFmpeg als externes Programm auf                       |

---

## 5. Schritt-für-Schritt-Ablauf

### Schritt 1 — Videosegmente filtern und sortieren

```python
v_segs = sorted(
    [s for s in segments if s.get("track", "").startswith("v")],
    key=lambda s: s["start"],
)
```

Nur die Video-Segmente (`track` beginnt mit `v`) werden gerendert; sie
werden nach ihrer Timeline-Position `start` sortiert. Die Audiospur entsteht
**nicht** aus separaten `a1`-Segmenten, sondern aus der **Tonspur der
Videoclips selbst** (siehe Schritt 4).

### Schritt 2 — Dateipfade auflösen

`_resolve_clips()` ([export.py:47](../backend/workers/export.py#L47)) macht
**eine** Datenbankabfrage für alle vorkommenden `clip_id`s und liefert eine
Zuordnung `clip_id → absoluter Dateipfad`. FFmpeg liest gleich die
**Originaldateien** aus `backend/uploads/` — nicht die Proxys.

> **🔬 Deep dive — Warum das Original und nicht der Proxy?**
> Der Proxy aus Phase 2 ist eine 960p-Version mit CRF 26 — gut genug für
> die Browser-Vorschau, aber zu verlustbehaftet für ein Endprodukt. Der
> Export soll **volle Qualität** liefern, also liest FFmpeg die
> unveränderten Originale. Hier schließt sich der „Stammbaum": Die Pixel
> der finalen Datei stammen direkt von der Wurzel — dem hochgeladenen
> Roh-Video.

Fehlt ein Clip (Datei gelöscht), bricht der Job mit einer klaren
Fehlermeldung ab, statt einen kaputten Render zu erzeugen.

### Schritt 3 — Der FFmpeg-Befehl

`_build_ffmpeg_cmd()` ([export.py:62](../backend/workers/export.py#L62))
ist das Herz von Phase 4. Es konstruiert **einen einzigen** FFmpeg-Aufruf,
der die gesamte Timeline in einem Durchgang rendert.

#### 3.1 — Die Eingaben

Für **jedes** Segment wird ein eigener Input mit Schnitt-Parametern
angelegt:

```
-ss {mediaStart} -t {dauer} -i {dateipfad}
```

`-ss` springt im Quell-Clip an die Startzeit des Segments, `-t` begrenzt
die Länge. Ein Clip, der dreimal in der Timeline vorkommt, wird also dreimal
als Input geladen — jeweils mit anderem Ausschnitt.

#### 3.2 — Normalisierung jedes Streams

```
[i:v] scale=W:H:force_original_aspect_ratio=decrease,
      pad=W:H:(ow-iw)/2:(oh-ih)/2:color=black,
      setsar=1, fps=25, setpts=PTS-STARTPTS  [vi]
```

> **🔬 Deep dive — Warum jeder Stream normalisiert werden muss**
> Die Quell-Clips können **unterschiedliche Auflösungen, Seitenverhältnisse
> und Bildraten** haben. FFmpeg kann sie aber nur dann mit `xfade`
> verketten, wenn sie **identisch** sind. Drei Schritte stellen das her:
> - **`scale` + `pad`:** Das Bild wird in die Zielauflösung skaliert
>   (`force_original_aspect_ratio=decrease` verhindert Verzerrung) und der
>   Rest mit schwarzen Balken aufgefüllt (Letterboxing). `setsar=1` setzt
>   das Pixel-Seitenverhältnis auf quadratisch.
> - **`fps=25`:** vereinheitlicht die Bildrate — `xfade` verlangt
>   übereinstimmende Frameraten.
> - **`setpts=PTS-STARTPTS`:** setzt den Zeitstempel jedes Streams auf 0
>   zurück, damit alle Segmente bei null beginnen.

#### 3.3 — Sonderfall: nur ein Segment

Besteht die Timeline aus nur einem Segment, gibt es nichts zu verketten —
FFmpeg macht einen einfachen Durchlauf (Skalierung + Encoding, kein
Filter-Graph).

#### 3.4 — Die xfade-Kette (mehrere Segmente)

Bei n Segmenten werden sie paarweise mit `xfade` (Video) und `acrossfade`
(Audio) verkettet:

```
[v0][v1] xfade=transition=…:duration=…:offset=…  [xv1]
[xv1][v2] xfade=…                                 [xv2]
…                                                 [vout]
```

> **🔬 Deep dive — Die `offset`-Berechnung**
> `xfade` braucht drei Parameter: den Übergangs-`type`, die `duration` und
> den **`offset`** — den Zeitpunkt im **Ausgabe**-Strom, an dem der Übergang
> beginnt. Der Code führt einen kumulativen Zähler:
> ```python
> cumulative_offset += prev_dur - trans_dur
> ```
> Der Übergang beginnt also `trans_dur` Sekunden **vor** dem Ende des
> vorherigen Segments — denn während eines Crossfades laufen beide Clips
> gleichzeitig und überlappen sich um die Übergangsdauer. Ohne diese
> Korrektur würden sich die Segmentlängen aufsummieren und das Video würde
> mit jedem Übergang länger als beabsichtigt.

> **🔬 Deep dive — Die Sicherheitsmarge der Übergangsdauer**
> ```python
> max_safe_trans = min(prev_dur, next_dur) * 0.40 - 0.08
> ```
> Ein Übergang darf nie länger sein als die Clips, die er verbindet — sonst
> liest `xfade` über das Segmentende hinaus und es entstehen schwarze
> Frames oder Fehler. Der Code begrenzt die Übergangsdauer auf **maximal
> 40 % des kürzeren** der beiden Segmente, minus zwei Frames (0,08 s bei
> 25 fps) als Puffer. Die gewünschte Übergangsdauer aus der Timeline wird
> auf diesen sicheren Wert gedeckelt.
>
> **Harter Schnitt:** Hat ein Segment keinen Übergang, setzt der Code
> `trans_dur = 0.001` — ein technisch nötiger Mini-Wert, der praktisch
> einem harten Schnitt entspricht. So kann derselbe `xfade`-Mechanismus
> sowohl weiche als auch harte Schnitte erzeugen.

> **🔬 Deep dive — `acrossfade` spiegelt `xfade`**
> Für jeden Video-`xfade` wird ein paralleler Audio-`acrossfade` **mit
> exakt derselben Dauer** erzeugt. Würden Bild- und Tonübergänge
> unterschiedlich lang sein, liefen Bild und Ton mit jedem Schnitt weiter
> auseinander (Lippensynchronitäts-Fehler). Die Spiegelung hält Bild und
> Ton synchron.

#### 3.5 — Encoding-Einstellungen

```
-c:v libx264 -preset fast -crf 18
-c:a aac -b:a 192k
-movflags +faststart
```

> **🔬 Deep dive — Warum CRF 18 (Export) statt CRF 26 (Proxy)?**
> CRF (Constant Rate Factor) steuert die Qualität von H.264: niedriger =
> besser und größer. Der **Proxy** aus Phase 2 nutzt CRF 26 — bewusst
> verlustbehaftet, weil er nur Vorschau ist. Der **Export** nutzt CRF 18 —
> visuell nahezu verlustfrei, das passende Niveau für ein Endprodukt. Die
> Audio-Bitrate ist mit 192 kbit/s ebenfalls höher als beim Proxy
> (128 kbit/s). `+faststart` erlaubt sofortiges Abspielen im Browser.

### Schritt 4 — FFmpeg ausführen

```python
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
```

FFmpeg läuft als externer Prozess, maximal 10 Minuten. Bei Rückgabecode ≠ 0
werden die letzten 600 Zeichen der Fehlerausgabe geloggt und der Job auf
`"fehler"` gesetzt. Bei Erfolg liegt die Datei unter
`backend/outputs/export_{job_id[:8]}.mp4`.

### Schritt 5 — Job abschließen

`_update_job()` ([export.py:20](../backend/workers/export.py#L20)) setzt den
Job auf `status = "fertig"`, `fortschritt = 100` und schreibt in `ergebnis`
die `output_url` (`/outputs/export_…mp4`). Wie in Phase 2 wird dieselbe
Nachricht per Redis Pub/Sub an den WebSocket gesendet — der Browser zeigt
den fertigen Download-Link an.

---

## 6. Datenzustand nach Phase 4

- **Festplatte:** neue Datei `backend/outputs/export_{job_id}.mp4`.
- **PostgreSQL — Tabelle `jobs`:** die Export-Job-Zeile steht auf
  `"fertig"`, `ergebnis` enthält die `output_url`.
- **`clips`, `szenen`, `timelines`:** **unverändert** — Phase 4 liest nur.

---

## 7. Fehlerbehandlung

| Fehlerfall                       | Reaktion                                            |
| -------------------------------- | --------------------------------------------------- |
| Keine Segmente                   | Job `"fehler"`, Meldung „Keine Segmente angegeben"  |
| Keine Videosegmente              | Job `"fehler"`                                      |
| Quell-Clip nicht gefunden        | Job `"fehler"`, Anzahl fehlender Clips gemeldet     |
| FFmpeg-Rückgabecode ≠ 0          | Job `"fehler"`, letzte 200 Zeichen stderr gemeldet  |
| FFmpeg-Timeout (> 10 min)        | Job `"fehler"`, Meldung „FFmpeg Timeout"            |
| sonstige Exception               | Job `"fehler"`, Exception geloggt                   |

---

## 8. Weg B — „Senden an" eine professionelle Schnittsoftware

Endpunkt `POST /api/export/open-in`
([export.py:221](../backend/api/export.py#L221)). Statt eines fertigen
Videos exportiert CinAssist hier ein **Projekt** für ein professionelles
NLE (Non-Linear Editor): DaVinci Resolve, Premiere Pro oder Final Cut Pro.

### Ablauf

1. Das Frontend baut den Timeline-Inhalt als **FCPXML** (ein offenes
   XML-Format für Schnittprojekte) und sendet ihn an den Endpunkt.
2. Der Server schreibt die Datei nach
   `~/Documents/CinAssist_Exports/{name}_{timestamp}.fcpxml`.
3. Je nach Ziel-App:
   - **DaVinci Resolve:** Der Server versucht den **direkten Import** über
     die Resolve-Scripting-API (`backend/tools/davinci_import.py`). Gelingt
     das, erscheint die Timeline ohne manuellen Schritt direkt in Resolve.
   - **Premiere / Final Cut:** Diese Programme haben keine zuverlässige
     Headless-Import-Schnittstelle. Der Server startet die App und öffnet
     parallel den Finder mit der markierten Datei (`open -R`), sodass der
     Nutzer sie per Drag-and-Drop hineinziehen kann.

> **🔬 Deep dive — Warum FCPXML?**
> FCPXML ist das Austauschformat von Apple Final Cut Pro, wird aber **auch
> von DaVinci Resolve und Premiere Pro importiert**. Ein einziges Format
> erreicht damit alle drei großen NLEs. Es beschreibt die Timeline rein
> deklarativ (welcher Clip, welcher Ausschnitt, welche Position) — keine
> Pixel, nur Verweise. Der Profi öffnet so den **KI-Schnitt-Vorschlag** in
> seinem Werkzeug und schneidet von dort aus weiter.

> **🔬 Deep dive — Der DaVinci-Direktimport**
> `_davinci_direkt_import()` ([export.py:181](../backend/api/export.py#L181))
> nutzt die offizielle Python-Scripting-API von DaVinci Resolve. Läuft
> Resolve nicht, wird es per `open -a` gestartet und der Code wartet 12
> Sekunden auf den Hochlauf. Voraussetzung ist, dass in Resolve „External
> scripting" aktiviert ist. Scheitert der Direktimport, fällt der Code
> sauber auf den Finder-Reveal-Modus zurück — die Funktion ist also
> **robust degradierend**, nie ein harter Fehler.

---

## 9. Kernfragen für die Verteidigung

**„Warum läuft der Export asynchron über Celery wie Phase 2?"**
> Ein FFmpeg-Render kann Minuten dauern — viel zu lang für eine
> HTTP-Antwort, die nach ~30 s vom Browser abgebrochen würde. Daher
> dasselbe Producer/Consumer-Muster wie bei der Ingestion: sofortige
> Antwort mit `job_id`, Render im Hintergrund, Fortschritt per WebSocket.

**„Liest der Export den Proxy oder das Original?"**
> Das Original aus `backend/uploads/`. Der Proxy ist bewusst verlustbehaftet
> (CRF 26) und nur für die Vorschau gedacht. Der Export rendert in voller
> Qualität mit CRF 18.

**„Wie verhinderst du, dass Bild und Ton beim Schnitt auseinanderlaufen?"**
> Zu jedem Video-`xfade` wird ein `acrossfade` mit identischer Dauer
> erzeugt. Außerdem wird jeder Stream vor der Verkettung auf dieselbe
> Auflösung und 25 fps normalisiert und sein Zeitstempel zurückgesetzt.

**„Wie wird die Übergangsdauer begrenzt?"**
> Auf maximal 40 % des kürzeren der beiden beteiligten Segmente, minus zwei
> Frames Puffer. So liest `xfade` nie über ein Segmentende hinaus. Ein
> harter Schnitt wird als Mini-Übergang von 0,001 s realisiert.

**„Was ist der Unterschied zwischen ‚Export' und ‚Senden an'?"**
> ‚Export' (`/api/export`) rendert mit FFmpeg eine fertige MP4-Datei.
> ‚Senden an' (`/api/export/open-in`) schreibt ein FCPXML-Projekt und öffnet
> es in DaVinci, Premiere oder Final Cut — für Profis, die den KI-Vorschlag
> selbst weiterbearbeiten wollen.

---

## 10. Zusammenfassung in einem Satz

> Phase 4 nimmt die Timeline aus Phase 3, löst jedes Segment auf seine
> Original-Videodatei auf, baut daraus einen einzigen FFmpeg-Befehl mit
> normalisierten Streams und einer xfade/acrossfade-Kette, rendert in voller
> Qualität (CRF 18) eine MP4-Datei nach `backend/outputs/` — und bietet
> alternativ den Export als FCPXML-Projekt direkt in eine professionelle
> Schnittsoftware.

---

*Stand: 2026-05-22. Direkt aus dem Quellcode rekonstruiert.*
*Teil der Bachelorarbeit CinAssist — Abschluss der vierteiligen Pipeline.*
