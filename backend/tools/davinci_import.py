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


def baue_projekt(fcpxml_pfad: str, source_dir: str, timeline_name: str, manifest_pfad: str) -> bool:
    """
    Baut das CinAssist-Projekt DIREKT über die Resolve-API — ohne FCPXML-Import
    (Resolve 20 lehnte via Scripting selbst minimale FCPXML-Dateien ab; Befund 21.08.).

    modus "projekt": Bins je Szene + Metadaten (Scene/Shot/Take) + Beat-Marker +
    AutoSyncAudio, danach Timeline per CreateEmptyTimeline/AppendToTimeline.
    modus "timeline": nur Medien flach importieren + Timeline bauen.

    Timeline-Regeln: alle Video-Items mediaType=1 (nur Bild — der Kamera-Ton ist
    stumm), der alignierte WAV je V1-Segment als Audio-Item auf A1; Overlay-Spuren
    (V2+) werden nach dem Einfügen deaktiviert (stumme Coverage bleibt aus).
    """
    import json
    import time as _t

    with open(manifest_pfad, encoding="utf-8") as f:
        manifest = json.load(f)
    fps = float(manifest.get("fps") or 24.0)
    modus = manifest.get("mode") or "projekt"

    resolve = _connect()
    if resolve is None:
        return False
    pm = resolve.GetProjectManager()
    if not timeline_name:
        timeline_name = f"CinAssist {_t.strftime('%H%M%S')}"
    proj = pm.CreateProject(timeline_name)
    suffix = 2
    while proj is None and suffix < 50:
        proj = pm.CreateProject(f"{timeline_name} ({suffix})")
        suffix += 1
    if proj is None:
        print("FEHLER: Konnte kein Projekt erstellen.", file=sys.stderr)
        return False
    try:
        proj.SetSetting("timelineFrameRate", str(int(round(fps))))
    except Exception:
        pass
    media_pool = proj.GetMediaPool()
    root = media_pool.GetRootFolder()

    metadata = manifest.get("metadata") or {}
    marker = manifest.get("marker") or {}

    def item_metadaten(it, p):
        try:
            md = metadata.get(p)
            if md:
                it.SetMetadata("Scene", md.get("Scene", ""))
                it.SetMetadata("Shot", md.get("Shot", ""))
                it.SetMetadata("Take", md.get("Take", ""))
                it.SetClipProperty("Scene", md.get("Scene", ""))
                it.SetClipProperty("Shot", md.get("Shot", ""))
                it.SetClipProperty("Take", md.get("Take", ""))
            for m in (marker.get(p) or []):
                it.AddMarker(int(round(float(m["t"]) * fps)), "Blue", m.get("name", "Beat"), m.get("note", ""), 1)
        except Exception as exc:
            print(f"WARNUNG: Metadaten/Marker: {exc}", file=sys.stderr)

    je_name: dict = {}

    def merke(items, pfade):
        namen = {os.path.basename(p): p for p in pfade}
        for it in items or []:
            try:
                n = it.GetClipProperty("File Name") or ""
                je_name[n] = it
                if n in namen:
                    item_metadaten(it, namen[n])
            except Exception:
                pass

    # ── Bins je Szene (nur Projekt-Modus) ──
    if modus == "projekt":
        for bin_name, pfade in sorted((manifest.get("bins") or {}).items()):
            ordner = media_pool.AddSubFolder(root, bin_name)
            if ordner is None:
                print(f"WARNUNG: Bin '{bin_name}' fehlgeschlagen.", file=sys.stderr)
                continue
            media_pool.SetCurrentFolder(ordner)
            vorhanden = [p for p in pfade if os.path.exists(p)]
            merke(media_pool.ImportMedia(vorhanden), vorhanden)

    # ── Fehlende Timeline-Medien flach nachimportieren ──
    tlm = manifest.get("timeline") or {}
    segs = tlm.get("segmente") or []
    benoetigt = {s["clip_path"] for s in segs} | {s["audio_path"] for s in segs if s.get("audio_path")}
    fehlt = sorted(p for p in benoetigt if os.path.basename(p) not in je_name and os.path.exists(p))
    if fehlt:
        media_pool.SetCurrentFolder(root)
        merke(media_pool.ImportMedia(fehlt), fehlt)

    # ── AutoSyncAudio (Projekt-Modus): korrigierte WAV-TCs, dann Sync-Clips ──
    if modus == "projekt":
        for wav_pfad, tc in (manifest.get("audio_tc") or {}).items():
            it = je_name.get(os.path.basename(wav_pfad))
            if it is not None:
                try:
                    it.SetClipProperty("Start TC", tc)
                except Exception as exc:
                    print(f"WARNUNG: Start-TC {os.path.basename(wav_pfad)}: {exc}", file=sys.stderr)
        try:
            sync_items = [it for n, it in je_name.items() if n.upper().endswith((".MOV", ".WAV"))]
            if sync_items and hasattr(media_pool, "AutoSyncAudio"):
                media_pool.AutoSyncAudio(sync_items, {"timecodeAccuracy": 1})
        except Exception as exc:
            print(f"WARNUNG: AutoSyncAudio: {exc}", file=sys.stderr)

    # ── Timeline direkt bauen ──
    tl = media_pool.CreateEmptyTimeline(timeline_name)
    suffix = 2
    while tl is None and suffix < 50:
        tl = media_pool.CreateEmptyTimeline(f"{timeline_name} ({suffix})")
        suffix += 1
    if tl is None:
        print("FEHLER: CreateEmptyTimeline fehlgeschlagen.", file=sys.stderr)
        return False
    proj.SetCurrentTimeline(tl)

    def spur_von(s):
        try:
            return int(str(s.get("track", "v1")).lstrip("vV") or 1)
        except ValueError:
            return 1
    max_spur = max([spur_von(s) for s in segs] or [1])
    while tl.GetTrackCount("video") < max_spur:
        if not tl.AddTrack("video"):
            break
    BASIS = int(round(3600 * fps))          # Timeline-Start 01:00:00:00
    def fr(sek):
        return int(round(float(sek) * fps))

    n_video = 0
    for spur in range(1, max_spur + 1):
        reihe = sorted([s for s in segs if spur_von(s) == spur], key=lambda s: float(s["start"]))
        infos, quell = [], []
        for s in reihe:
            it = je_name.get(os.path.basename(s["clip_path"]))
            if it is None:
                print(f"WARNUNG: Kein MediaPool-Item für {os.path.basename(s['clip_path'])}", file=sys.stderr)
                continue
            infos.append({"mediaPoolItem": it,
                          "startFrame": fr(s["media_start"]),
                          "endFrame": fr(float(s["media_start"]) + float(s["duration"])),
                          "trackIndex": spur, "recordFrame": fr(s["start"]) + BASIS,
                          "mediaType": 1})
            quell.append(s)
        if not infos:
            continue
        hinzu = media_pool.AppendToTimeline(infos) or []
        n_video += len(hinzu)
        for ti, s in zip(hinzu, quell):
            if not s.get("enabled", True):
                try:
                    ti.SetClipEnabled(False)
                except Exception:
                    pass

    # WAV-Audio der V1-Segmente auf A1
    a_infos = []
    for s in sorted([x for x in segs if spur_von(x) == 1 and x.get("audio_path")], key=lambda x: float(x["start"])):
        it = je_name.get(os.path.basename(s["audio_path"]))
        if it is None:
            continue
        a0 = max(0.0, float(s.get("audio_start") or 0.0))
        a_infos.append({"mediaPoolItem": it, "startFrame": fr(a0),
                        "endFrame": fr(a0 + float(s["duration"])),
                        "trackIndex": 1, "recordFrame": fr(s["start"]) + BASIS,
                        "mediaType": 2})
    n_audio = len(media_pool.AppendToTimeline(a_infos) or []) if a_infos else 0

    resolve.OpenPage("edit")
    print(f"OK: Projekt '{proj.GetName()}' — Timeline '{tl.GetName()}' per API gebaut "
          f"({n_video} Video-Items auf {max_spur} Spuren, {n_audio} WAV-Audio-Items"
          + (", Bins+Metadaten+Marker+AutoSync" if modus == "projekt" else "") + ").")
    return True


def main() -> int:
    if len(sys.argv) < 3:
        print("Aufruf: python -m backend.tools.davinci_import <fcpxml> <source_dir> [timeline_name] [projekt_manifest.json]", file=sys.stderr)
        return 2
    fcpxml_pfad = sys.argv[1]
    source_dir = sys.argv[2]
    timeline_name = sys.argv[3] if len(sys.argv) > 3 else ""
    manifest = sys.argv[4] if len(sys.argv) > 4 else ""
    if manifest:
        erfolg = baue_projekt(fcpxml_pfad, source_dir, timeline_name, manifest)
    else:
        erfolg = importiere_timeline(fcpxml_pfad, source_dir, timeline_name)
    return 0 if erfolg else 1


if __name__ == "__main__":
    sys.exit(main())
