# Wiedergabe-Engine

Ausführlich zu den Schichten **Uhr** und **Compositor**. Eine anpassbare Umsetzung liegt in
`assets/playback-engine.ts`. Dieses Dokument erklärt das *Warum* jeder Entscheidung, damit
sich die Umsetzung ändern lässt, ohne die Invarianten zu brechen.

## Inhalt
1. Die Hauptuhr
2. Der Clip-Auflöser
3. Der Vorrat an `<video>` und das Vorladen
4. Die Nachführung bei Abweichung
5. Reihenfolge der Schritte in einem Ausgabebild
6. Scrub und Sprung
7. Erweiterungen (Ton, mehrere Spuren, Effekte)

---

## 1. Die Hauptuhr

Eine einzige Instanz in der ganzen Anwendung. Sie läuft während der Wiedergabe in Echtzeit
und stützt sich auf `performance.now()`, also auf die Wanduhr, und nicht auf einen Zähler,
der Bild für Bild erhöht wird und dabei Fehler anhäufen würde.

Der Ablauf:

```
play():
  wallStart  = performance.now()
  frameStart = currentFrame
  Schleife rAF:
    elapsedSec   = (now − wallStart) / 1000
    currentFrame = frameStart + elapsedSec * fps
    onTick(currentFrame)
```

Warum `requestAnimationFrame` und nicht `setInterval`: rAF ist mit der Bildwiederholung des
Bildschirms abgestimmt (etwa 60 Hz) und pausiert, sobald der Tab verdeckt ist. Warum nicht
`video.timeupdate`: Es feuert nur etwa vier Mal je Sekunde, der Abspielkopf ruckelt dann.

**Invariante:** Der Abspielkopf der Oberfläche liest `currentFrame`. Er liest niemals
`video.currentTime`.

## 2. Der Clip-Auflöser

Eine reine Funktion: `(track, t) → { clip, sourceFrame } | null`.

```
für jeden Clip der Spur:
  wenn clip.timelineStart <= t < clip.timelineStart + clip.duration:
    sourceFrame = clip.sourceIn + (t − clip.timelineStart)
    return { clip, sourceFrame }
return null   // Lücke → gewolltes Schwarz
```

Vorausgesetzt sind nach `timelineStart` sortierte Clips ohne Überschneidung innerhalb einer
Spur. Werden Überschneidungen eines Tages erlaubt, etwa für Übergänge, liefert der Auflöser
eine Liste zu überlagernder Clips statt eines einzelnen.

`null` ist kein Fehler, sondern eine echte Lücke in der Timeline, die schwarz dargestellt
wird. Die *unerwünschten* schwarzen Bilder treten an den **Grenzen** zwischen zwei
benachbarten Clips auf und stammen vom Laden, nicht vom Auflöser (siehe §3).

## 3. Der Vorrat an `<video>` und das Vorladen

Das Problem: `video.src` zu wechseln und danach `video.currentTime` zu setzen, läuft
asynchron. Solange das neue Medium lädt und sich positioniert, zeigt das Element Schwarz.
An den Clipgrenzen ist das bei jedem Schnitt sichtbar.

Die Lösung: **zwei** `<video>`-Elemente, `active` (sichtbar) und `standby` (verborgen,
vorgewärmt).

- **Vorladen:** In jedem Bild wird `t + lookahead` betrachtet, etwa eine Sekunde im Voraus.
  Hat der dann aktive Clip ein anderes `src` als der laufende, wird dieses `src` in
  `standby` geladen und auf seinen Einstiegspunkt gesetzt. So ist der Wartestand lange vor
  dem Schnitt bereit.
- **Wechsel:** Überschreitet `t` die Grenze, liegt das gewünschte `src` bereits im
  Wartestand. `active` und `standby` werden getauscht, was nur Deckkraft und Verweis
  betrifft. Das geschieht sofort und ohne schwarzes Bild.
- **Nicht vorgewärmter Fall**, etwa unmittelbar nach einem harten Sprung: Der Clip wird
  direkt in `active` geladen. Ein kurzes Schwarz ist dann möglich, nach einem beliebigen
  Sprung unvermeidbar, bei gewöhnlichen Schnitten aber ausgeschlossen.

Für eine Spur genügen zwei Elemente. Für Übergänge, bei denen zwei Clips gleichzeitig
sichtbar sind, ist ein etwas größerer Vorrat vorzusehen.

## 4. Die Nachführung bei Abweichung

Die Feinheit, die fast alle übersehen.

Während der Wiedergabe hat die `<video>` ihre eigene Dekodier-Uhr. Sie und die `MasterClock`
laufen leicht auseinander. Die Versuchung: `video.currentTime = sourceSeconds` in jedem Bild
zu setzen, um „synchron zu bleiben". **Das ist die Falle**, denn jedes Schreiben von
`currentTime` löst einen erneuten Sprung aus, der die flüssige Wiedergabe unterbricht und
dauerhaftes Ruckeln erzeugt.

Der richtige Weg:

```
abweichung = |video.currentTime − gewünschteQuellSekunden|
wenn (pausiert) ODER (abweichung > schwelle):   // Schwelle etwa 0,15 s
  video.currentTime = gewünschteQuellSekunden
sonst:
  nichts tun, das Video allein laufen lassen
```

- **Pausiert oder beim Scrub** wird immer nachgeführt, denn dort ist genau das Bild unter
  dem Abspielkopf gefragt.
- **Während der Wiedergabe** läuft es weiter und wird nur bei zu großer Abweichung
  nachgeführt. Zur Schwelle: zu klein führt zu häufigen Sprüngen und damit zu Ruckeln, zu
  groß zu hör- oder sichtbarem Versatz. Etwa 0,15 s ist ein guter Anfangswert, der sich
  verfeinern lässt.

## 5. Reihenfolge der Schritte in einem Ausgabebild

Bei jedem Takt der Uhr, in dieser Reihenfolge:

1. Den aktiven Clip bei `t` auflösen (§2).
2. Bei `null` die aktive `<video>` verbergen, also Schwarz. Andernfalls:
   a. Weicht das gewünschte `src` vom `src` der aktiven `<video>` ab, wird gewechselt,
      per Tausch bei vorgewärmtem Wartestand, sonst durch direktes Laden.
   b. Nachführung bei Abweichung auf der aktiven `<video>` (§4).
   c. Den nächsten Clip in den Wartestand vorladen (§3).
3. Die Oberfläche benachrichtigen, damit der Abspielkopf auf `t` rückt.

## 6. Scrub und Sprung

Ein Scrub ist schlicht `clock.seek(frame)`:

```
seek(frame):
  currentFrame = frame
  frameStart   = frame            // die Uhr neu verankern
  wallStart    = performance.now()
  onTick(frame)                   // sofortige Ausgabe, auch im Stillstand
```

Beim Scrub im Stillstand ist das genaue Bild gefragt, weshalb der Zweig mit der
unbedingten Nachführung aus §4 greift. Bei sehr schnellem Scrub lässt sich das Vorladen
abschalten und nur Schlüsselbilder anzeigen, um reaktionsfähig zu bleiben, und beim
Loslassen dann das genaue Bild darstellen.

## 7. Erweiterungen

- **Ton:** dasselbe Prinzip. Ein `AudioContext` mit `AudioBufferSourceNode`, oder die
  Tonspuren der `<video>`, folgen `t`. Ton reagiert empfindlicher auf Abweichung als Bild,
  deshalb eine feinere Schwelle und am besten eine Planung auf der Uhr des Audiokontexts,
  die ihrerseits an der `MasterClock` ausgerichtet ist.
- **Mehrere Videospuren:** Der Auflöser liefert einen aktiven Clip *je Spur*, der
  Compositor stapelt von der untersten zur obersten Spur, wobei die obere Vorrang hat, oder
  wendet Mischmodi an.
- **Effekte und Übergänge** greifen im Compositor, also in Schritt 2 der Ausgabe, nach dem
  Auflösen und vor der Anzeige. Sie lesen das Modell und verändern es nicht.
