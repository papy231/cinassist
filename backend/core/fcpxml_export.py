"""
CinAssist — eigener FCPXML-1.8-Generator (Ersatz für OTIO, das unter Python 3.14
am eigenen Plugin-Manifest scheitert — „bad any cast“).

Kann, was der OTIO-Weg nie konnte:
  • absolute Timeline-Positionen mit Lücken (Gaps im Spine)
  • V2+ als CONNECTED CLIPS (lanes) — Resolve legt sie auf eigene Videospuren
  • Overlay-Clips deaktiviert (enabled="0") — stumme Coverage bleibt stumm
  • pro Spine-Clip der ALIGNIERTE WAV als Audio-Lane (Offsets aus dem Sync-Modell)
  • Beat-Marker + Notizen pro Clip

Segment-Dict:
  clip_path, clip_name, start (Timeline s), media_start, duration, track ("v1"…),
  enabled (bool), clip_dauer (Quelldauer s),
  audio_path/audio_dauer/audio_start (optional, WAV + Startzeit im WAV),
  marker: [{"t": Mediazeit s, "name": str, "note": str}], note (optional)
"""
from __future__ import annotations

import time
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

# ~/Movies statt ~/Documents: Documents ist iCloud-synchronisiert — bei vollem iCloud
# werden Dateien evakuiert („Fehler“-Badge) und Resolve kann sie nicht lesen.
EXPORT_DIR = Path.home() / "Movies" / "CinAssist_Exports"


def _fmt_zeit(sekunden: float, fps: float) -> str:
    """FCPXML-Rationalzeit, frame-genau: '<frames*fd_num>/<fd_den>s'."""
    frames = round(sekunden * fps)
    fd = Fraction(1 / fps).limit_denominator(100000) if fps != int(fps) else Fraction(1, int(fps))
    zaehler = frames * fd.numerator
    return f"{zaehler}/{fd.denominator}s" if fd.denominator != 1 else f"{zaehler}s"


def schreibe_fcpxml(segmente: list[dict], name: str, fps: float = 24.0,
                    breite: int = 1920, hoehe: int = 1080) -> Path:
    """Erzeugt die FCPXML-Datei in EXPORT_DIR und liefert den Pfad."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in name if c.isalnum() or c in "_- ") or "Timeline"
    out = EXPORT_DIR / f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.fcpxml"

    fd = Fraction(1 / fps).limit_denominator(100000) if fps != int(fps) else Fraction(1, int(fps))
    frame_dur = f"{fd.numerator}/{fd.denominator}s"

    # ── Assets (Video + Audio) deduplizieren ──
    assets: dict[str, dict] = {}   # pfad → {"id", "dauer", "audio": bool}
    def asset_id(pfad: str, dauer: float, hat_video: bool, hat_audio: bool) -> str:
        if pfad not in assets:
            assets[pfad] = {"id": f"a{len(assets) + 1}", "dauer": max(dauer, 1.0),
                            "video": hat_video, "audio": hat_audio}
        else:
            assets[pfad]["dauer"] = max(assets[pfad]["dauer"], dauer)
        return assets[pfad]["id"]

    for s in segmente:
        asset_id(s["clip_path"], float(s.get("clip_dauer") or (s["media_start"] + s["duration"])), True, True)
        if s.get("audio_path"):
            asset_id(s["audio_path"], float(s.get("audio_dauer") or 0.0), False, True)

    # ── Spine (V1) + Connected (V2+) ──
    def spur_nr(s: dict) -> int:
        try:
            return int(str(s.get("track", "v1")).lstrip("vV") or 1)
        except ValueError:
            return 1
    v1 = sorted([s for s in segmente if spur_nr(s) == 1], key=lambda s: float(s["start"]))
    obere = sorted([s for s in segmente if spur_nr(s) > 1], key=lambda s: float(s["start"]))
    if not v1:
        raise ValueError("Keine V1-Segmente — der Spine braucht mindestens einen Clip.")

    def clip_xml(s: dict, *, lane: int | None, offset_s: float, eltern_einr: int) -> list[str]:
        aid = assets[s["clip_path"]]["id"]
        attrs = [f'ref="{aid}"', f'offset="{_fmt_zeit(offset_s, fps)}"',
                 f'name={quoteattr(s.get("clip_name") or Path(s["clip_path"]).stem)}',
                 f'start="{_fmt_zeit(float(s["media_start"]), fps)}"',
                 f'duration="{_fmt_zeit(float(s["duration"]), fps)}"']
        if lane is not None:
            attrs.append(f'lane="{lane}"')
        if not s.get("enabled", True):
            attrs.append('enabled="0"')
        ein = "  " * eltern_einr
        zeilen = [f"{ein}<asset-clip {' '.join(attrs)}>"]
        for m in (s.get("marker") or []):
            t = float(m["t"])
            if not (float(s["media_start"]) <= t < float(s["media_start"]) + float(s["duration"])):
                continue
            zeilen.append(f'{ein}  <marker start="{_fmt_zeit(t, fps)}" duration="{frame_dur}" '
                          f'value={quoteattr(m["name"])}'
                          + (f' note={quoteattr(m["note"])}' if m.get("note") else "") + "/>")
        if s.get("note"):
            zeilen.append(f"{ein}  <note>{escape(str(s['note']))}</note>")
        zeilen.append(f"{ein}</asset-clip>")
        return zeilen

    spine: list[str] = []
    cursor = 0.0
    for i, s in enumerate(v1):
        st = float(s["start"])
        if st > cursor + 0.5 / fps:
            spine.append(f'      <gap name="Gap" offset="{_fmt_zeit(cursor, fps)}" '
                         f'duration="{_fmt_zeit(st - cursor, fps)}"/>')
        # Kinder dieses Spine-Clips: obere Clips, deren Start in [st, ende) fällt,
        # + der alignierte WAV als Audio-Lane. Kind-Offset = Eltern-`start` (Mediazeit)
        # + (Kind-Timeline-Pos − Eltern-Timeline-Pos)  — FCPXML-Semantik.
        ende = st + float(s["duration"])
        kinder: list[str] = []
        if s.get("audio_path"):
            a = assets[s["audio_path"]]
            kinder.append(
                f'        <asset-clip ref="{a["id"]}" lane="-1" '
                f'offset="{_fmt_zeit(float(s["media_start"]), fps)}" '
                f'name={quoteattr(Path(s["audio_path"]).stem)} '
                f'start="{_fmt_zeit(max(0.0, float(s.get("audio_start") or 0.0)), fps)}" '
                f'duration="{_fmt_zeit(float(s["duration"]), fps)}"/>')
        for o in obere:
            ost = float(o["start"])
            if st - 1e-6 <= ost < ende:
                off = float(s["media_start"]) + (ost - st)
                kinder.extend(clip_xml(o, lane=spur_nr(o) - 1, offset_s=off, eltern_einr=4))
        haupt = clip_xml(s, lane=None, offset_s=st, eltern_einr=3)
        # Kinder (Lanes) VOR den eigenen Markern/Notes, dann die Schließ-Zeile des Parents
        spine.extend([haupt[0], *kinder, *haupt[1:]])
        cursor = max(cursor, ende)

    gesamt = max(float(s["start"]) + float(s["duration"]) for s in segmente)

    res_zeilen = [f'    <format id="r1" name="FFVideoFormat{hoehe}p{int(round(fps))}" '
                  f'frameDuration="{frame_dur}" width="{breite}" height="{hoehe}"/>']
    for pfad, a in assets.items():
        res_zeilen.append(
            f'    <asset id="{a["id"]}" name={quoteattr(Path(pfad).stem)} '
            f'src={quoteattr(Path(pfad).as_uri())} start="0s" '
            f'duration="{_fmt_zeit(a["dauer"], fps)}" '
            f'hasVideo="{1 if a["video"] else 0}" hasAudio="{1 if a["audio"] else 0}"'
            + (' format="r1"' if a["video"] else "") + "/>")

    xml = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        '<fcpxml version="1.8">',
        "  <resources>",
        *res_zeilen,
        "  </resources>",
        "  <library>",
        f'    <event name="CinAssist">',
        f'      <project name={quoteattr(name)}>',
        f'        <sequence format="r1" duration="{_fmt_zeit(gesamt, fps)}" tcStart="0s" '
        f'tcFormat="NDF" audioLayout="stereo" audioRate="48k">',
        "          <spine>",
        *["      " + z if not z.startswith(" ") else z for z in spine],
        "          </spine>",
        "        </sequence>",
        "      </project>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
    ])
    out.write_text(xml, encoding="utf-8")
    return out
