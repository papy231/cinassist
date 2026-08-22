---
name: cinassist-nle-architecture
description: >-
  Referenzarchitektur für CinAssist, den nichtlinearen Videoschnitt (NLE) nach Art
  von Final Cut oder DaVinci, den Pascal in Next.js mit dem HTML5-video-Element baut.
  ZU VERWENDEN, SOBALD es um den Videoeditor, das Schnittassistenzsystem, die
  Timeline, den Abspielkopf, die Synchronität von Abspieler und Timeline, um Clips,
  Scrub, Wiedergabe, schwarze Bilder, Ruckeln oder um das Ergänzen oder Beheben
  einer Schnittfunktion geht (Trennen, Trimmen, Ripple, Roll, Slip, Slide,
  Übergänge). Auch dann verwenden, wenn die Stichwörter auf Französisch
  (montage, timeline, lecture) oder auf Englisch auftauchen (NLE, timeline,
  playhead, playback engine, compositor, non-linear editor). Dieses Skill immer
  heranziehen, BEVOR Code zur Wiedergabe oder zur Timeline geschrieben wird, auch
  wenn die Anfrage einfach wirkt: Die meisten Fehler in CinAssist stammen aus einem
  falschen Denkmodell, das hier richtiggestellt wird.
---

# CinAssist — NLE-Architektur

CinAssist ist ein Schnittassistenzsystem, gebaut in **Next.js mit dem HTML5-Element
`<video>`** im Browser. Dieses Skill hält das richtige Denkmodell eines nichtlinearen
Schnitts fest, damit jeder Eingriff in den Editor stimmig bleibt und die bekannten Fehler
nicht neu entstehen: ruckelnder Abspielkopf, auseinanderlaufende Timeline, schwarze Bilder
an den Clipgrenzen.

## Die Grundregel (niemals verletzen)

**Die Timeline ist die EINZIGE Wahrheitsquelle. Eine einzige Uhr erzeugt die Zeit `t`.
Alles andere folgt `t`, niemals umgekehrt.**

Der Abspieler „spielt" kein Video. Er zeigt *das für `t` berechnete Bild*. Das Element
`<video>` ist nur eine Ausgabe unter mehreren — neben Abspielkopf, Wellenform und Ton —,
die `t` nachlaufen. Sobald dieses Verhältnis umgekehrt wird, sobald also die Timeline aus
der Zeit der `<video>` gesteuert wird, entstehen Versatz und Ruckeln. Das ist der
Architekturfehler Nummer eins.

## Das Modell in drei Schichten

Jede Funktion in CinAssist gehört zu **genau einer** dieser Schichten. Vor dem Schreiben
von Code die Schicht bestimmen, das verhindert 90 Prozent der Rückschritte.

1. **Modell (Wahrheitsquelle)** — die Datenstruktur der Sequenz: Spuren, Clips, jeder Clip
   als `{ src, sourceIn, timelineStart, duration }`. Die Zeit steht darin in **ganzen
   Einzelbildern**, nie in Gleitkommasekunden.
   → Die Schnittoperationen (Trennen, Trimmen, Ripple und weitere) sind **reine Umformungen
   dieses Modells**. Sie fassen die `<video>` NIEMALS an.

2. **Uhr (die Zeit `t`)** — eine einzige `MasterClock`, geführt von
   `requestAnimationFrame` (60 fps). Sie lässt `t` während der Wiedergabe fortschreiten und
   springt bei Scrub oder Sprung.
   → Der Abspielkopf liest `t` hier. Er liest NIEMALS `video.currentTime`.

3. **Compositor (die Ausgabe)** — in jedem Bild und für das laufende `t`: auflösen, welche
   Clips aktiv sind, die gewünschte Quellzeit berechnen, die richtige `<video>`
   positionieren, überlagern, anzeigen.
   → Hier leben der Vorrat an `<video>`, das Vorladen, die Nachführung bei Abweichung und
   später die Effekte und Übergänge.

```
   Modell  ──gelesen von──►  Compositor  ──erzeugt──►  angezeigtes Bild
      ▲                          ▲
      │                          │ liest t
   bearbeitet von           Uhr (t) ──gelesen von──► Abspielkopf der Oberfläche
   den Werkzeugen
```

## Die grundlegende Abbildung (darauf beruht die ganze Synchronität)

Für einen bei der Timeline-Zeit `t` aktiven Clip gilt:

```
sourceFrame = clip.sourceIn + (t − clip.timelineStart)
```

`sourceIn` ist der Einstiegspunkt in der Quelldatei, `timelineStart` die Position des Clips
auf der Timeline. Das sind zwei verschiedene Zeiten. Sie zu verwechseln bricht alles.

## Vorgehen bei einer Korrektur oder Ergänzung

1. **Die Schicht bestimmen** (Modell, Uhr oder Compositor). Betrifft die Anfrage mehrere
   Schichten, wird sie zerlegt.
2. **Die Grundregel prüfen**: Führt der geplante Weg dazu, dass die `<video>` die Timeline
   steuert? Dann ist er falsch, und es ist von der Uhr aus neu zu denken.
3. **In ganzen Einzelbildern bleiben** in der gesamten Logik. Erst im Kontakt mit der
   `<video>` (`video.currentTime`) und mit der Anzeige wird in Sekunden umgerechnet.
4. **Bei einem Fehler in Wiedergabe oder Synchronität** zuerst
   `references/common-bugs.md` heranziehen. Der Katalog verbindet jedes Symptom mit seiner
   Ursache und ihrer Behebung. Nicht das Symptom flicken.
5. **Für eine Schnittoperation** (Trennen, Trimmen, Ripple, Roll, Slip, Slide)
   `references/timeline-model.md` heranziehen. Jede ist dort als reine Umformung des
   Modells beschrieben.
6. **Für die Wiedergabe** (Uhr, Auflöser, Vorrat an `<video>`, Abweichung, Vorladen)
   `references/playback-engine.md` heranziehen. Eine anpassbare Umsetzung in TypeScript
   liegt in `assets/playback-engine.ts`.

## Fallstricke des Web-Elements `<video>`

- Den Abspielkopf **niemals** aus dem Ereignis `timeupdate` führen. Es feuert nur etwa vier
  Mal je Sekunde und lässt die Bewegung ruckeln. Stattdessen aus `requestAnimationFrame`.
- Während der Wiedergabe **niemals** in jedem Bild `video.currentTime = …` setzen. Jedes
  Schreiben löst einen erneuten Sprung aus und zerhackt die Wiedergabe. Die `<video>` allein
  laufen lassen und nur nachführen, wenn die Abweichung einen Schwellwert von etwa 0,15 s
  überschreitet.
- **Eine einzige `<video>` erzeugt schwarze Bilder** beim Clipwechsel, weil Laden und
  Springen asynchron sind. Einen **Vorrat** aus zwei `<video>` nutzen und den nächsten Clip
  im Voraus laden. Ein schwarzes Bild über einer *echten* Lücke der Timeline ist dagegen
  richtig.
- **Abweichung der Gleitkommazahlen**: Zeit in Gleitkommasekunden driftet. Die Zeit in
  ganzen Einzelbildern halten und erst im letzten Moment umrechnen.

## Referenzdateien

- `references/playback-engine.md` — Hauptuhr, Auflöser, Vorrat an `<video>`, Nachführung
  bei Abweichung, Vorladen. Für jede Arbeit an der Wiedergabe zu lesen.
- `references/timeline-model.md` — Datenmodell, Zeit in Einzelbildern und die
  Schnittoperationen (Trennen, Trimmen, Ripple, Roll, Slip, Slide) als reine Umformungen.
  Für jedes Schnittwerkzeug zu lesen.
- `references/common-bugs.md` — Katalog Symptom, Ursache, Behebung. Bei einem Fehler in
  Wiedergabe oder Synchronität zuerst zu lesen.
- `assets/playback-engine.ts` — Referenzumsetzung der Engine, im Next.js-Projekt anzupassen.
