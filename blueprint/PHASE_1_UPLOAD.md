# Phase 1 — Synchroner Upload

> Was passiert in dem Augenblick, in dem der Nutzer auf „Upload" klickt,
> bis die HTTP-Antwort den Browser erreicht.

**Dauer:** typischerweise unter einer Sekunde (dominiert von der
Schreibgeschwindigkeit auf die Festplatte).
**Charakter:** synchron, im Haupt-Thread des FastAPI-Servers.
**Ergebnis:** das Video liegt auf der Festplatte, zwei Datenbank-Einträge
sind angelegt, eine Hintergrund-Task wartet in Redis — die eigentliche
Analyse (Phase 2) hat **noch nicht begonnen**.

**Quelldatei:** [`backend/api/clips.py`](../backend/api/clips.py) ·
Funktion `clip_hochladen()` (Zeile 34).

---

## 1. Zielsetzung dieser Phase

Phase 1 ist die **Annahme-Phase**. Sie hat zwei strikte Anforderungen:

1. **Schnelligkeit** — die HTTP-Antwort muss in unter einer Sekunde
   zurückkommen. Ein Browser bricht eine Verbindung nach rund 30 Sekunden
   Standard-Timeout ab; eine vollständige Video-Analyse dauert dagegen
   mehrere Minuten.
2. **Verlässliche Übergabe** — am Ende von Phase 1 muss garantiert sein,
   dass das Video sicher gespeichert ist und ein Auftrag zur Weiter­
   verarbeitung existiert. Die Phase darf nicht „halbwegs" abschließen.

Aus diesen beiden Anforderungen ergibt sich die Architektur:
**annehmen, validieren, persistieren, einen Auftrag stellen, antworten**.
Die eigentliche Arbeit (die Analyse) wird **ausgelagert** und erst in
Phase 2 von einem separaten Prozess ausgeführt.

### Das Architektur-Prinzip: Request/Response vs. Background-Worker

Eine HTTP-Anfrage ist ein **synchroner Vertrag**: Der Client schickt eine
Anfrage und erwartet zeitnah eine Antwort. Lange Berechnungen passen nicht
in dieses Modell. Die etablierte Lösung ist das **Producer/Consumer-Muster**:

- Der **Producer** (FastAPI in Phase 1) nimmt die Aufgabe an, legt sie in
  eine Warteschlange und antwortet sofort.
- Der **Consumer** (Celery-Worker in Phase 2) entnimmt die Aufgabe der
  Warteschlange und führt sie in Ruhe aus.

Dieses Muster entkoppelt die Geschwindigkeit der Annahme von der Dauer der
Verarbeitung — die Grundlage jedes skalierbaren Web-Backends.

---

## 2. Auslöser

Der Nutzer klickt im Browser-Frontend (Next.js, `src/app/editor`) auf den
Upload-Button. Die Frontend-Funktion `uploadClip(file, quelle)` in
`src/lib/api.ts` baut ein `FormData`-Objekt mit zwei Feldern:

| Feld     | Typ    | Wert                                  |
| -------- | ------ | ------------------------------------- |
| `datei`  | binär  | der gesamte Video-Inhalt (MP4-Bytes)  |
| `quelle` | string | `"A"` oder `"B"`                      |

Die Felder `datei` und `quelle` sind das **einzige Eingangs-Datum** der
gesamten Pipeline — alles Weitere wird daraus abgeleitet. Anschließend
sendet der Browser einen HTTP-Request:

```
POST /api/clips/upload HTTP/1.1
Host: localhost:8001
Content-Type: multipart/form-data; boundary=----WebKitFormBoundaryXyZ
Content-Length: 4194304

------WebKitFormBoundaryXyZ
Content-Disposition: form-data; name="quelle"

A
------WebKitFormBoundaryXyZ
Content-Disposition: form-data; name="datei"; filename="opening.mp4"
Content-Type: video/mp4

[binäre MP4-Bytes …]
------WebKitFormBoundaryXyZ--
```

> **Warum „Quelle A / B"?** CinAssist ist auf den Schnitt aus **zwei
> Quellmaterialien** ausgelegt (z. B. zwei Kamerawinkel oder zwei
> Aufnahmen). Die Quelle wird pro Clip festgehalten und später vom
> KI-Schnitt-Algorithmus genutzt, um bewusst zwischen den Quellen zu
> alternieren (siehe Phase 3).

---

## 3. Sequenzdiagramm

```
NUTZER        BROWSER        uvicorn       FastAPI      Pydantic    SQLAlchemy   PostgreSQL    Celery      Redis
  │              │              │             │            │            │            │           │          │
  │─click──────▶│              │             │            │            │            │           │          │
  │              │─POST multipart──────────▶│             │            │            │           │          │
  │              │              │─parse HTTP─▶│            │            │            │           │          │
  │              │              │             │─validate Eingaben─────▶│            │           │          │
  │              │              │             │◀─OK / 422───────────────│            │           │          │
  │              │              │             │─schreibe 1-MB-Chunks → /uploads/{uuid}.mp4                   │
  │              │              │             │─Clip-Objekt + Job-Objekt erstellen─▶│            │           │          │
  │              │              │             │─await db.commit()──────────────────▶│─INSERT clip─▶│        │          │
  │              │              │             │                                     │─INSERT job──▶│        │          │
  │              │              │             │◀────────────────────────────────────│◀─COMMIT OK───│        │          │
  │              │              │             │─ingestion_pipeline.delay(clip,job)──────────────────────────▶│        │
  │              │              │             │                                                              │─push msg─▶│
  │              │              │             │◀──────────────────────────────────────────────task_id ──────│        │
  │              │              │             │─UPDATE job.celery_task_id ──────────▶│─UPDATE──────▶│        │          │
  │              │◀─200 OK + JSON {clip_id, job_id, …}───│                            │            │           │          │
  │◀─„Upload erfolgreich"───────│              │             │            │            │           │          │
  │              │─open WebSocket /ws/jobs/{job_id}──────▶│             │            │           │          │
  │              │              │             │─subscribe Redis-Kanal „job:{id}"────────────────────────────▶│
  │              │              │             │  (wartet auf Nachrichten von Phase 2)                         │
```

---

## 4. Beteiligte Open-Source-Bibliotheken

Phase 1 bindet zehn Open-Source-Bibliotheken in einer fest definierten
Reihenfolge ein. Jede hat eine **klar abgegrenzte Verantwortung** — eine
austauschbare Komponente ändert nur ihren eigenen Bereich.

| #  | Bibliothek           | Version | Kategorie        | Aufgabe in einem Satz                                                   | Lizenz          |
| -- | -------------------- | ------- | ---------------- | ----------------------------------------------------------------------- | --------------- |
| 1  | **uvicorn**          | 0.30    | ASGI-Server      | Nimmt HTTP-Bytes von Port 8001 entgegen und leitet sie an FastAPI weiter | BSD-3           |
| 2  | **FastAPI**          | 0.115   | Web-Framework    | Routet die URL `/api/clips/upload` zur Handler-Funktion                 | MIT             |
| 3  | **Pydantic**         | 2.x     | Validation       | Prüft automatisch die Typen der Eingabe-Parameter                       | MIT             |
| 4  | **python-multipart** | 0.0.9   | Body-Parser      | Dekodiert den `multipart/form-data`-Body, extrahiert die Datei          | Apache 2        |
| 5  | **SQLAlchemy**       | 2.0     | ORM              | Übersetzt Python-Objekte (Clip, Job) in SQL-Befehle                     | MIT             |
| 6  | **asyncpg**          | 0.29    | DB-Treiber       | Nicht-blockierende Verbindung zwischen Python und PostgreSQL            | Apache 2        |
| 7  | **PostgreSQL**       | 16      | Datenbank        | Speichert die Tabellen `clips` und `jobs` mit ACID-Garantien            | PostgreSQL Lic. |
| 8  | **Celery**           | 5.4     | Task-Queue       | `.delay()` sendet die Ingestion-Task in die Redis-Warteschlange         | BSD-3           |
| 9  | **Redis** / redis-py | 7 / 5.0 | Broker + Pub/Sub | Speichert die Warteschlange und kanalisiert spätere Pub/Sub-Updates     | BSD-3 / MIT     |
| 10 | **websockets**       | 12.0    | Echtzeit-Kanal   | Hält die WebSocket-Verbindung offen, um Phase-2-Updates zu empfangen    | BSD-3           |

Eine ausführliche Begründung jeder Bibliothek findet sich in
[`PHASE_0_BIBLIOTHEKEN.md`](PHASE_0_BIBLIOTHEKEN.md).

---

## 5. Schritt-für-Schritt-Ablauf

### Schritt 1 — uvicorn empfängt die Bytes

Der ASGI-Server uvicorn lauscht auf `127.0.0.1:8001`. Er empfängt die
TCP-Segmente mit den HTTP-Headern und dem Request-Body, parst den
HTTP-Frame und erzeugt ein `Request`-Objekt, das er über das
**ASGI-Protokoll** an die FastAPI-Anwendung übergibt.

> **🔬 Deep dive — Was ist ASGI?**
> ASGI (Asynchronous Server Gateway Interface) ist der async-fähige
> Nachfolger von WSGI. WSGI definiert einen *synchronen* Funktionsaufruf
> `app(environ, start_response)` — ein Request blockiert einen Worker, bis
> er fertig ist. ASGI definiert stattdessen eine *coroutine*
> `async app(scope, receive, send)`. Dadurch kann ein einzelner
> Prozess während eines `await` (z. B. Warten auf die Datenbank) andere
> Requests bearbeiten. Genau das macht den nicht-blockierenden Upload mit
> gleichzeitigem WebSocket möglich.

### Schritt 2 — FastAPI routet zur Handler-Funktion

FastAPI wertet die URL `/api/clips/upload` und die HTTP-Methode `POST` aus,
konsultiert die intern aufgebaute Routing-Tabelle und findet die mit
`@router.post("/upload")` dekorierte Funktion `clip_hochladen`
([clips.py:34](../backend/api/clips.py#L34)).

Der `APIRouter` wurde mit dem Präfix `/api/clips` erzeugt, daher ist die
vollständige URL `/api/clips` + `/upload`.

### Schritt 3 — python-multipart + Pydantic validieren die Eingaben

Bevor `clip_hochladen` aufgerufen wird, geschehen zwei Dinge automatisch:

- **python-multipart** dekodiert den `multipart/form-data`-Body nach
  RFC 7578: Es findet die `boundary`-Trennlinien, extrahiert die zwei
  deklarierten Felder und stellt die Datei als `UploadFile`-Stream bereit
  (nicht als RAM-Block — wichtig für große Dateien).
- **Pydantic / FastAPI** prüft die Signatur:
  `datei: UploadFile = File(...)` und `quelle: str = Form(...)`.
  Fehlt ein Feld oder hat es den falschen Typ, antwortet FastAPI
  **vor** dem Funktionsaufruf automatisch mit `HTTP 422`.

Die Funktion `clip_hochladen` führt anschließend **drei fachliche
Validierungen** durch ([clips.py:48–63](../backend/api/clips.py#L48)):

```python
if quelle not in ("A", "B"):                       # clips.py:48
    raise HTTPException(400, "Quelle muss 'A' oder 'B' sein.")

endung = Path(dateiname).suffix.lower()
if endung not in ERLAUBTE_ENDUNGEN:                # {.mp4,.mov,.avi,.mkv,.webm}
    raise HTTPException(400, "Dateiformat … nicht unterstützt.")

if datei.size and datei.size > MAX_DATEIGROESSE:   # 5 GB
    raise HTTPException(400, "Datei zu groß (max. 5 GB).")
```

> **🔬 Deep dive — Warum zwei Validierungs-Ebenen?**
> Die Pydantic-Ebene prüft die **technische Form** (Typ, Vorhandensein).
> Die Handler-Ebene prüft die **fachliche Gültigkeit** (erlaubte Werte,
> Geschäftsregeln). Diese Trennung ist bewusst: technische Fehler liefern
> `422` (Standard-FastAPI), fachliche Fehler liefern `400` mit einer
> deutschsprachigen, für den Nutzer verständlichen Meldung. Die
> Datei-Endungs-Whitelist (`ERLAUBTE_ENDUNGEN`) ist eine **Whitelist**,
> keine Blacklist — ein sichereres Muster, weil unbekannte Formate
> standardmäßig abgelehnt werden.

### Schritt 4 — Streaming-Schreiben der Datei auf die Festplatte

Eine UUID wird generiert (`clip_id`), der Zielpfad gebildet
(`backend/uploads/{uuid}{endung}`), und die Datei wird in **1-MB-Chunks**
geschrieben ([clips.py:66–76](../backend/api/clips.py#L66)):

```python
clip_id   = str(uuid.uuid4())
ziel_pfad = UPLOAD_DIR / f"{clip_id}{endung}"

try:
    with open(ziel_pfad, "wb") as f:
        while chunk := await datei.read(1024 * 1024):   # 1 MB
            f.write(chunk)
except Exception as e:
    ziel_pfad.unlink(missing_ok=True)   # Aufräumen bei Fehler
    raise HTTPException(500, f"Datei konnte nicht gespeichert werden: {e}")
```

> **🔬 Deep dive — Warum 1-MB-Chunks und nicht alles auf einmal?**
> Würde man `await datei.read()` ohne Argument aufrufen, läge die
> **gesamte** Datei im Arbeitsspeicher. Bei der zulässigen Maximalgröße von
> 5 GB würde der Server-Prozess sofort die RAM-Grenze sprengen und vom
> Betriebssystem per **OOM-Killer** (Out-Of-Memory) beendet. Streaming löst
> das: Es wird immer nur 1 MB gelesen, geschrieben und der RAM wieder
> freigegeben. **Der RAM-Verbrauch bleibt konstant bei ~1 MB**, unabhängig
> davon, ob die Datei 4 MB oder 5 GB groß ist. Das `await` vor `read`
> bedeutet außerdem, dass der Server während des Lesens andere Requests
> bedienen kann.
>
> **Warum die UUID als Dateiname?** Der Originalname (`opening.mp4`) wird
> in der DB gespeichert, aber **nicht** als Dateiname verwendet. Zwei
> Nutzer könnten Dateien mit demselben Namen hochladen; eine UUID ist
> garantiert eindeutig und verhindert das Überschreiben. Zudem schließt
> sie **Path-Traversal-Angriffe** aus (ein Originalname wie
> `../../etc/passwd` kann keinen Schaden anrichten).

### Schritt 5 — Zwei Datenbank-Einträge in einer Transaktion

Es werden zwei ORM-Objekte erstellt und der Session hinzugefügt
([clips.py:81–102](../backend/api/clips.py#L81)):

```python
clip = Clip(
    id=clip_id, dateiname=dateiname, dateipfad=str(ziel_pfad),
    quelle=quelle, dateigroesse=dateigroesse, status="hochgeladen",
)
db.add(clip)

job_id = str(uuid.uuid4())
job = Job(
    id=job_id, typ="ingestion", clip_id=clip_id,
    status="wartend", fortschritt=0,
    nachricht="Job wurde erstellt, warte auf Start...",
)
db.add(job)

await db.commit()       # atomares INSERT beider Zeilen
```

**SQLAlchemy** wandelt diese Operationen in zwei SQL-`INSERT`-Anweisungen
um, **asyncpg** transportiert sie über die TCP-Verbindung an PostgreSQL,
und der Datenbankserver schreibt beide Zeilen in **einer einzigen
Transaktion** — atomar: entweder beide oder keine.

> **🔬 Deep dive — Warum sind Clip und Job eine Einheit?**
> Ein `Clip` ohne `Job` wäre ein Video, das nie analysiert wird. Ein `Job`
> ohne `Clip` wäre ein Auftrag ohne Gegenstand. Beide ergeben nur
> **gemeinsam** Sinn. Indem sie in derselben Transaktion geschrieben
> werden, ist garantiert: Es gibt nie einen halben Zustand in der
> Datenbank. Das ist das **A** in **ACID** (Atomicity).

### Schritt 6 — Celery-Task an Redis übergeben

```python
task = ingestion_pipeline.delay(clip_id, job_id)   # clips.py:105
```

`.delay()` ist eine **fire-and-forget**-Operation. Intern:

1. Celery serialisiert die Argumente `(clip_id, job_id)` als JSON.
2. Erzeugt eine Task-Nachricht mit eindeutiger `task_id`, dem Task-Namen
   `"cinassist.ingest"` und den Argumenten.
3. Sendet diese Nachricht per `LPUSH` in die Redis-Liste `celery`.
4. Gibt sofort ein `AsyncResult`-Objekt zurück (FastAPI wartet **nicht**
   auf die Ausführung).

Welcher Prozess die Task wann ausführt, ist FastAPI gleichgültig — diese
Verantwortung liegt beim Celery-Worker, der separat gestartet wird
(`celery -A backend.core.celery_app worker --pool=solo`).

### Schritt 7 — Task-ID nachtragen (zweiter Commit)

```python
job.celery_task_id = task.id   # clips.py:108
await db.commit()              # clips.py:109 — zweiter Commit
```

> **🔬 Deep dive — Warum zwei Commits?**
> Die `task_id` existiert erst, **nachdem** `.delay()` aufgerufen wurde —
> und `.delay()` braucht eine bereits existierende `job_id`. Es entsteht
> eine Reihenfolge-Abhängigkeit: erst Job committen → dann Task abschicken
> → dann die zurückgegebene `task_id` nachtragen. Der zweite Commit
> verknüpft Job und Celery-Task, sodass man später nachvollziehen kann,
> welche Celery-Task zu welchem Job gehört. Eine elegantere Variante würde
> die `task_id` vorab generieren — das ist eine dokumentierte mögliche
> Verbesserung.

### Schritt 8 — Antwort an den Browser

```python
return {
    "clip_id":   clip_id,
    "job_id":    job_id,
    "dateiname": "opening.mp4",
    "quelle":    "A",
    "groesse_mb": 4.0,
    "nachricht": "Video hochgeladen. Analyse wird gestartet...",
}
```

FastAPI serialisiert dieses Dictionary per Pydantic in JSON und sendet
`HTTP 200 OK`. Die zentrale Information für den Browser ist die `job_id` —
mit ihr kann er den Fortschritt von Phase 2 verfolgen.

### Schritt 9 — Browser öffnet die WebSocket-Verbindung

Der Browser nimmt `job_id` aus der Antwort und öffnet eine zweite
Verbindung:

```javascript
new WebSocket(`ws://localhost:8001/ws/jobs/${job_id}`)
```

Diese Verbindung sendet einen HTTP-Upgrade-Request
(`Upgrade: websocket`), den FastAPI annimmt. Sie bleibt **dauerhaft
offen**. Server-seitig abonniert der WebSocket-Handler
([`backend/api/websocket.py`](../backend/api/websocket.py)) den
Redis-Pub/Sub-Kanal `job:{job_id}`. Sobald Phase 2 startet und
Fortschritts-Nachrichten publiziert, leitet der Handler diese an den
Browser weiter.

In diesem Moment endet Phase 1.

---

## 6. Datenzustand nach Phase 1

### 6.1 Auf der Festplatte

```
backend/uploads/
└── 8c1a6add-ea7d-425e-a9ff-201027079973.mp4   ← Roh-Video, umbenannt nach UUID
```

Die Datei ist **unverändert** — keine Konvertierung, kein Proxy, keine
Audio-Extraktion. Nur das Original unter neuem Namen.

### 6.2 PostgreSQL — Tabelle `clips`

| Spalte         | Wert nach Phase 1                      |
| -------------- | -------------------------------------- |
| `id`           | `8c1a6add-…` (UUID)                    |
| `dateiname`    | `opening.mp4`                          |
| `dateipfad`    | `/…/backend/uploads/8c1a6add-….mp4`    |
| `quelle`       | `A`                                    |
| `dauer`        | `NULL` *(noch nicht gemessen)*         |
| `aufloesung`   | `NULL`                                 |
| `bildrate`     | `NULL`                                 |
| `codec`        | `NULL`                                 |
| `dateigroesse` | `4194304`                              |
| `status`       | `"hochgeladen"`                        |

### 6.3 PostgreSQL — Tabelle `jobs`

| Spalte           | Wert nach Phase 1                          |
| ---------------- | ------------------------------------------ |
| `id`             | `b0e8f24c-…` (UUID)                        |
| `typ`            | `"ingestion"`                              |
| `clip_id`        | `8c1a6add-…` (Fremdschlüssel auf clips)    |
| `celery_task_id` | `e95a31bd-…` (von Celery vergeben)         |
| `status`         | `"wartend"`                                |
| `fortschritt`    | `0`                                        |
| `nachricht`      | `"Job wurde erstellt, warte auf Start..."` |
| `ergebnis`       | `NULL`                                     |

### 6.4 In Redis

- Liste `celery`: **eine** Task-Nachricht mit JSON-Payload
  `{task: "cinassist.ingest", args: [clip_id, job_id], id: …}`.
- Pub/Sub-Kanal `job:{job_id}`: noch keine Nachrichten.

### 6.5 Im Speicher des FastAPI-Prozesses

- Eine offene WebSocket-Verbindung zum Browser, abonniert auf den
  Redis-Kanal `job:{job_id}`.

### 6.6 Tabellen `szenen` und `timelines`

**Leer.** Sie werden erst in Phase 2 bzw. Phase 3 befüllt.

---

## 7. Was nach Phase 1 noch nicht geschehen ist

Diese Liste belegt, dass die Auslagerungs-Entscheidung **bewusst** ist:

- ❌ Das Video wurde **nicht** dekodiert.
- ❌ **Keine Metadaten** (Dauer, Auflösung, Codec) sind ausgelesen.
- ❌ **Kein Proxy** für die Browser-Vorschau wurde erzeugt.
- ❌ **Keine Audiospur** wurde extrahiert.
- ❌ **Keine Transkription** wurde durchgeführt.
- ❌ **Keine Szenen** wurden erkannt.
- ❌ **Keine PIL-Analyse**, keine CLIP-Embeddings, keine LLaVA-Beschreibungen.
- ❌ Die Tabelle `szenen` ist **leer**.

All dies geschieht in Phase 2, ausgeführt vom Celery-Worker in einem
separaten Prozess.

---

## 8. Fehlerbehandlung in Phase 1

| Fehlerfall                          | Reaktion des Codes                                              |
| ----------------------------------- | --------------------------------------------------------------- |
| Ungültige Quelle (nicht A/B)        | `HTTP 400`, kein Schreiben auf Festplatte                       |
| Nicht erlaubtes Dateiformat         | `HTTP 400`, kein Schreiben auf Festplatte                       |
| Datei > 5 GB                        | `HTTP 400`                                                      |
| Schreibfehler auf der Festplatte    | Datei wird gelöscht (`unlink`), `HTTP 500`                      |
| Redis nicht erreichbar bei `.delay()` | Exception — Clip+Job sind bereits geschrieben (bekannte Limitation) |

> **Bekannte Limitation:** Schlägt `.delay()` fehl (Redis offline), bleiben
> Clip- und Job-Zeile in der DB, ohne dass je eine Analyse startet. Ein
> produktionsreifer Code würde hier die Transaktion zurückrollen oder einen
> Retry-Mechanismus mit exponentieller Verzögerung einsetzen. Dies ist in
> der Methodik als bewusste Vereinfachung des Demonstrators dokumentiert.

---

## 9. Kernfragen für die Verteidigung

**„Warum ist Phase 1 nicht selbst die Analyse?"**
> Eine HTTP-Anfrage wird vom Browser nach rund 30 Sekunden abgebrochen,
> eine vollständige Videoanalyse dauert aber mehrere Minuten. Synchrone
> Ausführung würde fast immer in einem Timeout enden. Deshalb wird die
> Analyse über Celery in einen Hintergrund-Prozess ausgelagert; der Browser
> erhält sofort eine `job_id` und beobachtet den Fortschritt per WebSocket.

**„Warum schreibst du in 1-MB-Chunks?"**
> Das vollständige Laden einer bis zu 5 GB großen Datei in den RAM würde
> den Server-Prozess per OOM-Killer beenden. Streaming in 1-MB-Chunks hält
> den RAM-Verbrauch konstant niedrig, unabhängig von der Dateigröße.

**„Warum eine UUID statt des Originalnamens als Dateiname?"**
> Eindeutigkeit (kein Überschreiben bei Namensgleichheit) und Sicherheit
> (Ausschluss von Path-Traversal-Angriffen). Der Originalname bleibt in der
> Spalte `dateiname` erhalten.

**„Warum zwei `db.commit()`?"**
> Die `celery_task_id` existiert erst nach `.delay()`, und `.delay()`
> braucht die bereits committete `job_id`. Der zweite Commit trägt die
> Task-ID nach und verknüpft Job und Celery-Task.

**„Warum WebSocket statt Polling?"**
> Polling (`GET /api/jobs/{id}` alle 2 s) erzeugt unnötigen Netzwerkverkehr
> und liefert verzögerte Updates. Ein WebSocket hält eine Verbindung offen
> und erlaubt **server-initiierte** Nachrichten — der Worker kann
> Fortschritt ohne Verzögerung pushen.

---

## 10. Zusammenfassung in einem Satz

> Phase 1 nimmt das Video entgegen, validiert es, speichert es per Streaming
> auf der Festplatte, legt atomar zwei Datenbank-Einträge an (Clip und Job),
> stellt einen Ingestion-Auftrag in die Redis-Warteschlange, antwortet mit
> `200 OK` und einer Job-ID und öffnet einen WebSocket-Kanal für die
> Echtzeit-Verfolgung von Phase 2 — alles typischerweise in unter einer
> Sekunde.

**→ Weiter mit [`PHASE_2_INGESTION.md`](PHASE_2_INGESTION.md).**

---

*Stand: 2026-05-22. Direkt aus dem Quellcode rekonstruiert.*
*Teil der Bachelorarbeit CinAssist.*
