# Timeline-Modell und Schnittoperationen

Ausführlich zur Schicht **Modell**, der Wahrheitsquelle. Leitgedanke: Jede Schnittoperation
ist eine **reine Umformung des Modells**. Sie fasst weder die `<video>` noch die Uhr an.
Sobald das Modell geändert ist, zeigt der Compositor beim nächsten Takt von selbst das
richtige Ergebnis.

## Inhalt
1. Datenstruktur
2. Zeit in ganzen Einzelbildern
3. Die Schnittoperationen
4. Zu wahrende Invarianten

---

## 1. Datenstruktur

```ts
type Frames = number; // Zeit in GANZEN Einzelbildern

interface Clip {
  id: string;
  src: string;            // Quellmedium
  sourceIn: Frames;       // Einstiegspunkt IM Quellmedium
  timelineStart: Frames;  // Position AUF der Timeline
  duration: Frames;       // Dauer auf der Timeline
}

interface Track { id: string; clips: Clip[]; } // sortiert, ohne Überschneidung
interface Timeline { fps: number; tracks: Track[]; }
```

Hinweise:
- Ein Feld `sourceOut` gibt es **nicht**, es ergibt sich aus `sourceIn + duration`.
  Ein Feld weniger zu pflegen heißt eine Invariante weniger, die brechen kann.
- `src` darf mehrfach auf dieselbe Datei zeigen, wenn ein Medium in mehrere Clips
  zerlegt ist. Das ist normal und beabsichtigt.

## 2. Zeit in ganzen Einzelbildern

Alle internen Zeiten sind ganze Einzelbilder. Die Gründe:
- Gleitkommazahlen in Sekunden häufen Abweichungen an. Fehler von einem halben Einzelbild
  zerstören die Synchronität und machen Schnitte unpräzise.
- Das Einzelbild ist die natürliche Einheit eines Schnittsystems: Ein Schnitt liegt
  *auf* einem Einzelbild.

Umgerechnet wird nur an den Rändern des Systems:
```
sekunden = frames / fps            // zu <video>.currentTime und zur Anzeige
frames   = round(sekunden * fps)   // aus einer Eingabe in Sekunden
```

Zeitcode zur Anzeige `HH:MM:SS:FF`:
```
FF = frames % fps
SS = floor(frames / fps) % 60
MM = floor(frames / (fps*60)) % 60
HH = floor(frames / (fps*3600))
```

## 3. Die Schnittoperationen

Jede nimmt das Modell (oder eine Spur) und liefert ein neues Modell zurück. Werden diese
Operationen **unveränderlich** geschrieben, also mit neuen Objekten als Ergebnis, wird
Rückgängig und Wiederholen einfach: Die Historie ist dann nur ein Stapel von Zuständen
oder Änderungssätzen des Modells.

### Trennen (am Abspielkopf schneiden)
Beim Einzelbild `t` wird der aktive Clip durch zwei Clips ersetzt.
```
links  = { ...clip, duration: t − clip.timelineStart }
rechts = { ...clip,
           sourceIn:      clip.sourceIn + (t − clip.timelineStart),
           timelineStart: t,
           duration:      clip.duration − (t − clip.timelineStart) }
```
Zu beachten ist die Verschiebung von `sourceIn` im rechten Teil. Das ist die grundlegende
Abbildung, angewandt auf den Schnitt.

### Trimmen (eine Kante beschneiden)
Verschiebt die Eingangs- oder Ausgangskante eines Clips, **ohne** die Nachbarn zu
verschieben. Je nach Modus bleibt eine Lücke oder es entsteht eine Überschneidung.
- Eingang um `Δ` Einzelbilder trimmen: `sourceIn += Δ`, `timelineStart += Δ`,
  `duration −= Δ`.
- Ausgang um `Δ` trimmen: `duration += Δ`, begrenzt durch das verfügbare Medium.

### Ripple (trimmen und schließen)
Ein Trimmen an Ein- oder Ausgang, gefolgt vom **Verschieben aller nachfolgenden Clips**
derselben Spur, um die Lücke zu schließen oder aufzunehmen. Das ist das schiebende
Trimmen: Die Gesamtdauer der Sequenz ändert sich.

### Roll (einen Schnitt verschieben)
Bei zwei benachbarten Clips A|B wird A um `Δ` verlängert und B um `Δ` verkürzt oder
umgekehrt. Die Grenze wandert, die Gesamtdauer bleibt gleich. Das ist ein Ausgangstrimmen
an A und ein Eingangstrimmen an B, miteinander verbunden.

### Slip (den Inhalt verschieben)
Ändert, *was zu sehen ist*, ohne Position und Dauer auf der Timeline zu verändern:
nur `sourceIn += Δ`, begrenzt durch das Medium. Das Fenster im Quellmedium wandert, der
Platz auf der Timeline bleibt derselbe.

### Slide (die Position verschieben)
Verschiebt einen Clip entlang der Timeline und passt beide Nachbarn an. Der Clip behält
Inhalt und Dauer, `timelineStart += Δ`, der linke Nachbar gewinnt `Δ`, der rechte verliert
`Δ` oder umgekehrt.

### Verschieben und Ablegen
Ändert `timelineStart` und gegebenenfalls die Spur. Die Regel bei Überschneidung ist zu
entscheiden: überschreiben, einfügen (Ripple) oder ablehnen.

## 4. Zu wahrende Invarianten

Nach **jeder** Operation ist zu prüfen, am besten über eine Funktion `normalize()`, die am
Ende jeder Operation aufgerufen wird:

- **Clips sortiert** nach `timelineStart` auf jeder Spur.
- **Keine Überschneidung** innerhalb einer Spur, ausgenommen Übergangsbereiche, die
  gesondert behandelt werden.
- **Grenzen des Mediums**: `sourceIn >= 0` und `sourceIn + duration <=` Dauer des
  Quellmediums. Einzelbilder, die es nicht gibt, lassen sich nicht zeigen.
- **Dauer größer als null**: Clips mit Dauer null, die beim Trimmen oder Trennen entstehen,
  werden entfernt.
- **Stimmige `fps`**: Hat ein Medium eine andere Bildrate als die Sequenz, ist die Regel
  früh festzulegen, also entweder anpassen oder die Einzelbilder zur Laufzeit umrechnen.
  Zwei Bezugssysteme dürfen sich nicht stillschweigend vermischen.

Solange diese Invarianten halten, zeigt der Compositor immer etwas Richtiges an, denn er
tut nichts anderes, als das Modell zu lesen.
