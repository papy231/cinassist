# Katalog der wiederkehrenden Fehler

**Zuerst** lesen, wenn ein Fehler bei Wiedergabe oder Synchronisation auftritt. Fast alle
Symptome in CinAssist gehen auf eine Handvoll Ursachen zurück. Die Ursache beheben,
nicht das Symptom.

Aufbau: **Symptom → Ursache → Behebung**.

---

## Abspielkopf ruckelt oder springt stoßweise
**Ursache:** Der Abspielkopf wird aus `video.timeupdate` (etwa 4 Hz) bewegt oder aus einem
React-Zustand, der zu selten aktualisiert wird.
**Behebung:** Den Abspielkopf aus der `MasterClock` mit `requestAnimationFrame` (60 fps)
führen. Er liest `currentFrame`, niemals `video.currentTime`. Für die Position in Pixeln:
`(currentFrame / fps) * pixelProSekunde`.

## Timeline und Abspieler laufen auseinander
**Ursache:** Zwei konkurrierende Wahrheitsquellen — die Zeit der `<video>` **und** die Zeit
der Timeline streiten darum, wer führt.
**Behebung:** Nur eine Hauptuhr. Die `<video>` folgt `t` (und wird über die Abweichung
nachgeführt), sie gibt `t` niemals vor. Jede Stelle entfernen, an der
`video.currentTime` den Zustand der Timeline setzt.

## Schwarze Löcher beim Clipwechsel
**Ursache:** Eine einzige `<video>`, deren `src` an der Schnittstelle gewechselt wird. Laden
und Springen laufen asynchron und hinterlassen ein schwarzes Bild.
**Behebung:** Vorrat aus zwei `<video>` und Vorladen des nächsten Clips im Wartestand
(etwa eine Sekunde im Voraus), danach sofortiger Wechsel. Siehe
`playback-engine.md` §3.

## Wiedergabe hakt, ständige Mikroaussetzer
**Ursache:** `video.currentTime` wird während der Wiedergabe in jedem Bild gesetzt, was
ein dauerndes Neuspringen auslöst.
**Behebung:** Während der Wiedergabe die `<video>` allein laufen lassen und nur
nachführen, wenn `|video.currentTime − gewünschteQuellzeit|` einen Schwellwert von etwa
0,15 s überschreitet. Siehe §4 der Wiedergabe-Engine.

## Schwarzes Bild nach einem Sprung oder Scrub, aber nicht an den Schnitten
**Ursache:** Der Zielclip war nicht vorgewärmt und wird unmittelbar in die aktive
`<video>` geladen.
**Behebung:** Nach einem beliebigen Sprung teilweise unvermeidbar. Abmildern lässt es sich,
indem das zuletzt bekannte Bild stehen bleibt, bis das neue bereit ist (`readyState >= 2`),
statt Schwarz zu zeigen. Beim Scrub das genaue Bild erst beim Loslassen darstellen.

## Der Abspielkopf springt, oder das Bild eilt um ein Einzelbild vor oder nach
**Ursache:** Die Zeit wird als Gleitkommazahl in Sekunden gehalten und driftet, oder eine
Umrechnung verwechselt Einzelbild und Sekunde.
**Behebung:** Alle Zeiten als ganze Einzelbilder halten und erst im Kontakt mit der
`<video>` und der Anzeige in Sekunden umrechnen. Die `round()`-Aufrufe prüfen.

## Nach Trennen oder Trimmen erscheint der falsche Ausschnitt
**Ursache:** `sourceIn` wurde beim Schneiden nicht neu berechnet. Der rechte Teil einer
Trennung muss sein `sourceIn` verschieben.
**Behebung:** Die Abbildung anwenden — für den rechten Teil einer Trennung bei `t` gilt
`sourceIn = clip.sourceIn + (t − clip.timelineStart)`. Siehe `timeline-model.md`.

## Ton läuft bei langen Clips gegenüber dem Bild weg
**Ursache:** Ton und Bild werden von zwei unabhängigen Uhren geführt, oder der Schwellwert
für die Nachführung ist für den Ton zu großzügig.
**Behebung:** Den Ton an der `MasterClock` ausrichten, mit einem feineren Schwellwert als
beim Bild, und ihn auf der Uhr des Audiokontexts planen.

## Der Clip springt an den Anfang zurück und spielt an der Grenze erneut
**Ursache:** Beim Wechsel wurde die neue `<video>` nicht auf `sourceIn` gesetzt, oder der
Sprung schlug fehl, weil `readyState` beim Vorladen noch zu niedrig war.
**Behebung:** Beim Vorladen auf `loadedmetadata` springen, falls die Metadaten noch nicht
vorliegen, und die Position unmittelbar nach dem Wechsel erneut prüfen.

## Ein Tab im Hintergrund bringt alles aus dem Tritt
**Ursache:** `requestAnimationFrame` wird gedrosselt oder ausgesetzt, sobald der Tab
verdeckt ist, während die `<video>` sich anders verhalten kann.
**Behebung:** Eine Uhr auf Grundlage von `performance.now()` findet beim Zurückkehren von
selbst wieder zusammen, weil sie die tatsächlich vergangene Zeit misst. Wer strenges
Verhalten möchte, hält die Wiedergabe bei `visibilitychange` an.

---

## Allgemeines Vorgehen bei der Fehlersuche

1. **Welche Schicht?** (Modell, Uhr oder Compositor). Ein falsches Bild deutet meist auf
   das Modell und seine Abbildung, ruckelnde Bewegung auf die Uhr, Schwarzbild und
   Ladeprobleme auf den Compositor.
2. **Ist die Grundregel eingehalten?** Jede Stelle suchen, an der `video.currentTime` den
   Zustand der Timeline beeinflusst. Dort liegt fast immer der Fehler.
3. **Einzelbilder oder Sekunden?** Die Umrechnungen prüfen und die Gleitkommazahlen
   aufspüren, die dort stehen, wo ganze Einzelbilder stehen müssten.
