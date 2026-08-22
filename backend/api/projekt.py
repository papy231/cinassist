"""Projektverwaltung: ein neues, vollständig leeres Projekt anlegen.

Ein Projekt ist in CinAssist eine eigene Datenbank samt eigenem Medienordner und
eigener Warteschlange. Das Anlegen übernimmt das Skript `neues_projekt.sh` im
Wurzelverzeichnis; diese Schnittstelle ruft es auf, statt seine Logik ein zweites
Mal zu führen. Der Name wird als eigenes Argument übergeben, nicht über eine
Kommandozeile zusammengesetzt, damit er nichts ausführen kann.

Der Wechsel in ein neu angelegtes Projekt verlangt einen Neustart der Dienste,
weil Datenbank und Medienordner beim Start aus der Umgebung gelesen werden. Die
Antwort nennt deshalb den Befehl, mit dem sich das Projekt öffnen lässt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import DATABASE_URL, DATA_DIR
from backend.core.database import get_db

router = APIRouter(prefix="/api/projekt", tags=["projekt"])

WURZEL = Path(__file__).resolve().parents[2]
ANLEGE_SKRIPT = WURZEL / "neues_projekt.sh"


class NeuesProjekt(BaseModel):
    name: str


def _datenbankname() -> str:
    """Name der Datenbank aus der Verbindungszeichenfolge, ohne Zugangsdaten."""
    return DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]


def _anzeigename() -> str:
    """Klarname des Projekts.

    Er steht in `projekt.json` im Datenordner, sofern das Projekt über
    `neues_projekt.sh` angelegt wurde. Für ältere Projekte wird ersatzweise der
    Datenbankname lesbar gemacht, statt einen Namen zu erfinden.
    """
    datei = Path(DATA_DIR) / "projekt.json"
    if datei.is_file():
        try:
            name = json.loads(datei.read_text(encoding="utf-8")).get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (ValueError, OSError):
            pass
    roh = _datenbankname()
    return roh[len("cinassist_"):].replace("_", " ") if roh.startswith("cinassist_") else roh


@router.get("")
async def projekt_info():
    """Welches Projekt gerade geöffnet ist."""
    return {
        "name": _anzeigename(),
        "datenbank": _datenbankname(),
        "medien": str(DATA_DIR),
        "anlegen_moeglich": ANLEGE_SKRIPT.is_file() and os.access(ANLEGE_SKRIPT, os.X_OK),
    }


@router.post("/neu")
async def projekt_anlegen(anfrage: NeuesProjekt):
    """Legt ein neues, leeres Projekt an und meldet den Befehl zum Öffnen."""
    name = (anfrage.name or "").strip()
    if not name:
        raise HTTPException(400, "Bitte einen Namen angeben.")
    if len(name) > 80:
        raise HTTPException(400, "Der Name ist zu lang, höchstens 80 Zeichen.")
    if not re.search(r"[A-Za-zÄÖÜäöüß0-9]", name):
        raise HTTPException(400, "Der Name braucht mindestens einen Buchstaben oder eine Ziffer.")
    if not ANLEGE_SKRIPT.is_file():
        raise HTTPException(500, "Das Skript neues_projekt.sh wurde nicht gefunden.")

    lauf = subprocess.run(
        [str(ANLEGE_SKRIPT), name],
        cwd=str(WURZEL),
        capture_output=True,
        text=True,
        timeout=180,
    )
    ausgabe = (lauf.stdout or "") + (lauf.stderr or "")
    if lauf.returncode != 0:
        # Die erste aussagekräftige Zeile ist die Begründung des Skripts.
        grund = next((z.strip() for z in ausgabe.splitlines() if z.strip()), "Unbekannter Fehler.")
        raise HTTPException(400, grund)

    treffer = re.search(r"(\./start_[A-Za-z0-9_]+\.sh)", ausgabe)
    startskript = treffer.group(1) if treffer else None
    return {
        "name": name,
        "startskript": startskript,
        "befehl": f"./stop_cinassist.sh && {startskript}" if startskript else None,
        "ausgabe": ausgabe.strip(),
    }


@router.get("/aufgaben")
async def aufgaben_uebersicht(db: AsyncSession = Depends(get_db)):
    """Überblick über die Hintergrundaufträge.

    Bislang ließ sich ein Auftrag nur einzeln über seine Kennung abfragen. Wer die
    Ansicht wechselte, während eine Auswertung lief, erfuhr von einem Fehlschlag
    nichts mehr. Diese Übersicht macht laufende und gescheiterte Aufträge sichtbar,
    damit die Oberfläche darauf hinweisen kann.
    """
    from sqlalchemy import select
    from backend.core.database import Job

    zeilen = (await db.execute(
        select(Job).order_by(Job.aktualisiert_am.desc().nullslast()).limit(200)
    )).scalars().all()

    laufend = [j for j in zeilen if j.status == "laeuft"]
    gescheitert = [j for j in zeilen if j.status == "fehler"]
    je_typ: dict[str, dict[str, int]] = {}
    for j in zeilen:
        je_typ.setdefault(j.typ, {}).setdefault(j.status or "unbekannt", 0)
        je_typ[j.typ][j.status or "unbekannt"] += 1

    def kurz(j) -> dict:
        return {
            "typ": j.typ,
            "status": j.status,
            "fortschritt": j.fortschritt,
            "nachricht": (j.nachricht or "")[:200],
            "aktualisiert_am": j.aktualisiert_am.isoformat() if j.aktualisiert_am else None,
        }

    return {
        "laufend": [kurz(j) for j in laufend[:20]],
        "gescheitert": [kurz(j) for j in gescheitert[:20]],
        "anzahl_laufend": len(laufend),
        "anzahl_gescheitert": len(gescheitert),
        "je_typ": je_typ,
    }


@router.get("/liste")
async def projekt_liste():
    """Alle angelegten Projekte, neuestes zuerst.

    Ein Projekt erkennt man an seinem Start-Skript im Wurzelverzeichnis. Der Klarname
    steht in `projekt.json` im jeweiligen Medienordner, sofern das Projekt über
    `neues_projekt.sh` entstanden ist; sonst wird der Datenbankname lesbar gemacht.
    Gewechselt wird über das Start-Skript, weil Datenbank und Medienordner beim Start
    aus der Umgebung gelesen werden.
    """
    aktuell = _datenbankname()
    eintraege = []
    for skript in sorted(WURZEL.glob("start_*.sh")):
        try:
            text = skript.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        db_treffer = re.search(r"localhost:5432/([A-Za-z0-9_]+)", text)
        daten_treffer = re.search(r'CINASSIST_DATA_DIR="([^"]+)"', text)
        port_treffer = re.search(r"--port\s+(\d+)", text)
        if not db_treffer:
            continue
        datenbank = db_treffer.group(1)
        # Ohne eigene Angabe nutzt ein Projekt den Standardordner im Projektverzeichnis.
        # Auf DATA_DIR zurückzufallen wäre falsch: das ist der Ordner des gerade
        # laufenden Projekts, und die Liste zeigte dann überall dessen Namen.
        medien = (Path(os.path.expandvars(daten_treffer.group(1))).expanduser()
                  if daten_treffer else WURZEL / "backend")
        name = datenbank[len("cinassist_"):].replace("_", " ") if datenbank.startswith("cinassist_") else datenbank
        info = medien / "projekt.json"
        if info.is_file():
            try:
                klar = json.loads(info.read_text(encoding="utf-8")).get("name")
                if isinstance(klar, str) and klar.strip():
                    name = klar.strip()
            except (ValueError, OSError):
                pass
        try:
            zuletzt = skript.stat().st_mtime
        except OSError:
            zuletzt = 0.0
        eintraege.append({
            "name": name,
            "datenbank": datenbank,
            "medien": str(medien),
            "port": int(port_treffer.group(1)) if port_treffer else None,
            "startskript": f"./{skript.name}",
            "befehl": f"./stop_cinassist.sh && ./{skript.name}",
            "geoeffnet": datenbank == aktuell,
            "zuletzt_geaendert": zuletzt,
        })
    eintraege.sort(key=lambda e: e["zuletzt_geaendert"], reverse=True)
    return {"aktuell": aktuell, "projekte": eintraege}


class ProjektWechsel(BaseModel):
    startskript: str


@router.post("/wechseln")
async def projekt_wechseln(anfrage: ProjektWechsel):
    """Öffnet ein anderes Projekt.

    Backend und Arbeiter werden neu gestartet, die Oberfläche bleibt stehen: alle
    Projekte hören auf demselben Port, getrennt sind Datenbank, Medienordner und
    Warteschlange. Die eigentliche Arbeit übernimmt `wechsel_projekt.sh`, angestoßen
    als eigenständiger Vorgang — dieser Dienst beendet sich dabei selbst.

    Der Name des Skripts wird gegen die tatsächlich vorhandenen Dateien geprüft und
    nicht als Kommandozeile zusammengesetzt.
    """
    name = Path(anfrage.startskript.strip()).name
    ziel = WURZEL / name
    erlaubt = {p.name for p in WURZEL.glob("start_*.sh")}
    if name not in erlaubt or not ziel.is_file():
        raise HTTPException(400, f"Unbekanntes Start-Skript: {name}")
    helfer = WURZEL / "wechsel_projekt.sh"
    if not helfer.is_file():
        raise HTTPException(500, "wechsel_projekt.sh wurde nicht gefunden.")

    protokoll = open("/tmp/cinassist_wechsel.log", "ab")
    subprocess.Popen(
        [str(helfer), f"./{name}"],
        cwd=str(WURZEL),
        stdout=protokoll, stderr=protokoll, stdin=subprocess.DEVNULL,
        start_new_session=True,          # überlebt das Ende dieses Dienstes
    )
    return {"gestartet": name, "hinweis": "Backend und Arbeiter starten neu. Die Seite lädt sich neu, sobald sie antworten."}
