"""
CinAssist — DaVinci Resolve Timeline-Import via Scripting-API.

Verbindet sich mit einer LAUFENDEN DaVinci-Resolve-Instanz und importiert
eine FCPXML-Datei direkt als Timeline — ohne manuellen File→Import-Schritt.

Voraussetzungen:
  • DaVinci Resolve läuft
  • In DaVinci: Preferences → System → General →
    "External scripting using" = Local
  • Das DaVinciResolveScript-Modul ist installiert (Standard bei Resolve)

Aufruf:
    python -m backend.tools.davinci_import <fcpxml_pfad> <source_clips_dir>

Exit-Code 0 = Timeline importiert, ≠0 = Fehler (Aufrufer macht Fallback).
"""

from __future__ import annotations

import os
import sys

# DaVinci-Scripting-Umgebung (macOS-Standardpfade)
_RESOLVE_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
_RESOLVE_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
_RESOLVE_MODULES = _RESOLVE_API + "/Modules/"


def _connect():
    """Stellt die Verbindung zu DaVinci Resolve her. None bei Fehlschlag."""
    os.environ.setdefault("RESOLVE_SCRIPT_API", _RESOLVE_API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", _RESOLVE_LIB)
    if _RESOLVE_MODULES not in sys.path:
        sys.path.append(_RESOLVE_MODULES)
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as exc:
        print(f"FEHLER: DaVinciResolveScript-Modul nicht gefunden: {exc}", file=sys.stderr)
        return None
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        print(
            "FEHLER: Keine Verbindung zu DaVinci Resolve. Läuft Resolve? "
            "Ist 'External scripting' in den Preferences aktiviert?",
            file=sys.stderr,
        )
    return resolve


def importiere_timeline(fcpxml_pfad: str, source_clips_dir: str, timeline_name: str = "") -> bool:
    """
    Importiert die FCPXML als Timeline in das aktuelle DaVinci-Projekt
    und setzt sie als aktive Timeline (auf der Edit-Page sichtbar).

    timeline_name: eindeutiger Name. ImportTimelineFromFile gibt None zurück,
    wenn eine Timeline mit gleichem Namen bereits existiert — daher MUSS der
    Name pro Import eindeutig sein (der Aufrufer übergibt einen Zeitstempel).
    """
    import time as _t

    resolve = _connect()
    if resolve is None:
        return False

    pm = resolve.GetProjectManager()

    # FRISCHES Projekt pro Import. Grund: ImportTimelineFromFile gibt None
    # zurück, wenn der MediaPool die Quellclips bereits enthält (Konflikt
    # bei Wiederholung). Ein leeres Projekt hat einen leeren MediaPool —
    # so gelingt der Import zuverlässig, auch beim 2., 3., n-ten Mal.
    if not timeline_name:
        timeline_name = f"CinAssist {_t.strftime('%H%M%S')}"
    projekt_name = timeline_name
    proj = pm.CreateProject(projekt_name)
    if proj is None:
        # Name existiert evtl. schon → Suffix anhängen
        suffix = 2
        while proj is None and suffix < 50:
            proj = pm.CreateProject(f"{projekt_name} ({suffix})")
            suffix += 1
    if proj is None:
        # Letzter Ausweg: aktuelles Projekt verwenden
        proj = pm.GetCurrentProject()
    if proj is None:
        print("FEHLER: Konnte kein Projekt öffnen/erstellen.", file=sys.stderr)
        return False

    media_pool = proj.GetMediaPool()
    if media_pool is None:
        print("FEHLER: MediaPool nicht verfügbar.", file=sys.stderr)
        return False

    # ImportTimelineFromFile braucht sourceClipsPath, damit die Original-
    # Mediendateien (UUID-benannt im uploads/-Ordner) gefunden und verlinkt
    # werden. Ohne diesen Pfad gibt die API None zurück.
    optionen = {
        "importSourceClipsToMediaPool": True,
        "sourceClipsPath": source_clips_dir,
        "timelineName": timeline_name,
    }
    timeline = media_pool.ImportTimelineFromFile(fcpxml_pfad, optionen)
    if timeline is None:
        print(
            "FEHLER: ImportTimelineFromFile gab None zurück "
            f"(Timeline-Name versucht: '{timeline_name}').",
            file=sys.stderr,
        )
        return False

    proj.SetCurrentTimeline(timeline)
    resolve.OpenPage("edit")
    print(f"OK: Timeline '{timeline.GetName()}' importiert und aktiviert.")
    return True


def main() -> int:
    if len(sys.argv) < 3:
        print("Aufruf: python -m backend.tools.davinci_import <fcpxml> <source_dir> [timeline_name]", file=sys.stderr)
        return 2
    fcpxml_pfad = sys.argv[1]
    source_dir = sys.argv[2]
    timeline_name = sys.argv[3] if len(sys.argv) > 3 else ""
    erfolg = importiere_timeline(fcpxml_pfad, source_dir, timeline_name)
    return 0 if erfolg else 1


if __name__ == "__main__":
    sys.exit(main())
