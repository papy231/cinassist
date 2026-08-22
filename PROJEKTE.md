# Zwei getrennte Projekte in einer Installation

CinAssist führt jeweils ein Projekt. Für ein zweites Projekt läuft eine eigene Instanz mit
eigener Datenbank, eigenem Medienordner und eigener Warteschlange. Die beiden Bestände
berühren sich an keiner Stelle.

## Ein neues Projekt anlegen

Entweder in der Oberfläche über **Datei → Neues Projekt…** oder im Terminal:

    ./neues_projekt.sh "Name des Projekts"

Das Skript legt eine eigene Datenbank, einen eigenen Medienordner und ein eigenes Start-Skript
an und sucht dafür einen freien Port und eine freie Redis-Datenbank. Bestehende Projekte bleiben
unberührt. Aus dem Namen wird eine Kennung gebildet, aus „Mein zweiter Film" also
`mein_zweiter_film`. Gibt es die Datenbank schon, bricht das Skript ab, statt etwas zu
überschreiben.

Beide Wege führen dasselbe Skript aus. Der Menüpunkt reicht den Namen an
`neues_projekt.sh` weiter und nennt anschließend den Befehl zum Öffnen, der zugleich in der
Zwischenablage liegt. Ein Wechsel ohne Neustart ist nicht möglich, weil Datenbank und
Medienordner beim Start aus der Umgebung gelesen werden.

Der Menüpunkt **Datei → Neue Timeline** leert dagegen nur die Timeline. Medien,
Drehbuch und Schnittpläne bleiben dabei erhalten. Für einen wirklich leeren Anfang ist das Skript
der richtige Weg.

## Umschalten

Im Programm über **Datei → Projekte öffnen…**. Ein Klick auf ein Projekt beendet Backend und
Arbeiter des laufenden und startet die des gewählten; die Oberfläche bleibt stehen und lädt
sich neu, sobald der neue Dienst antwortet. Das dauert etwa zehn Sekunden.

Im Terminal geht es auch:

    ./wechsel_projekt.sh ./start_pinky_promise.sh

Oder von Grund auf:

    ./stop_cinassist.sh
    ./start_pinky_promise.sh

**Alle Projekte hören auf Port 8001.** Das ist der Grund, warum der Wechsel ohne Neustart der
Oberfläche gelingt: sie muss ihr Ziel nie ändern. Unbedenklich ist es, weil ohnehin nur ein
Projekt zur Zeit geöffnet sein kann — Next.js lässt pro Projektordner nur einen
Entwicklungsserver zu. Getrennt bleiben Datenbank, Medienordner und Warteschlange.

Jedes Start-Skript setzt seine Angaben **ausdrücklich**, keine wird aus der Umgebung
übernommen. Beim Wechsel erbte ein Projekt sonst den Medienordner des vorherigen und legte
seine Vorschaudateien im fremden Verzeichnis ab.

## Pinky Promise, der Stand der Bachelorarbeit

| | |
|---|---|
| Datenbank | `cinassist` |
| Medien | `backend/uploads`, `backend/proxies`, `backend/thumbnails` |
| Warteschlange | Redis, Datenbank 0 |
| Backend | Port 8001 |

Die Vorschaudateien der 58 Clips liegen vollständig lokal in `backend/proxies`. Die Wiedergabe
im Schnittfenster greift auf sie zurück und braucht daher weder Netzverbindung noch das
Originalmaterial. Die Kameradateien liegen auf dem Datenträger `/Volumes/A004/ROHMAT_VIDEO`
und werden nur für die Ausgabe in voller Auflösung benötigt.

## Das zweite Projekt

| | |
|---|---|
| Datenbank | `cinassist_projekt2` |
| Medien | `~/cinassist_projekt2` |
| Warteschlange | Redis, Datenbank 1 |
| Backend | Port 8002 |

Die Trennung erfolgt über die Umgebungsvariablen `DATABASE_URL`, `CINASSIST_DATA_DIR` und
`REDIS_URL`, die in `backend/core/config.py` ausgewertet werden, sowie über
`CINASSIST_BACKEND_URL` für die Weiterleitungen der Oberfläche.

## Ein neues Projekt befüllen

1. Oberfläche öffnen und das Drehbuch als PDF oder TXT hochladen. Der Parser erkennt
   Szenenköpfe der Form `1. INT. KÜCHE – TAG` oder `SZENE 3`, Figurennamen in Großbuchstaben
   mit dem Dialog darunter sowie Übergänge wie `CUT TO:`. Er arbeitet rein regelbasiert.
2. Die Videos hochladen oder einen Ordner einlesen.
3. Die Auswertung läuft von selbst: Szenentrennung, Transkription, Bildbeschreibung,
   Sprechertrennung, Gesichtserkennung.

Die Zuordnung einer Aufnahme zur Drehbuchszene läuft über die **gesprochene Klappe**, nicht
über den Dateinamen. Fehlt sie, arbeiten Suche, Sprechertrennung und Gesichtserkennung
weiter, die drehbuchgestützte Schnittplanung greift dann aber nicht.

## Sicherung und Wiederherstellung

Eine Sicherung anlegen:

    pg_dump -h localhost -p 5432 -d cinassist -Fc -f sicherung.dump

Sie wieder einspielen, etwa auf einem anderen Rechner:

    createdb cinassist
    pg_restore --no-owner -d cinassist sicherung.dump

Die Sicherung umfasst die Datenbank, also Clips, Szenen, Sprecher, Drehbuch, Kontext und
sämtliche Schnittpläne. Die Vorschaudateien in `backend/proxies` gehören mit dazu und sind
getrennt zu kopieren.
