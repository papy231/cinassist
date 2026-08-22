"""
CinAssist — Cleanup automatique (Vague 2).

Erzeugt aus den Sprechzeiten der Whisper-Transkription (transkription_json):
    1. remove_silences: nur die gesprochenen Abschnitte, Pausen über dem Schwellwert entfallen
    2. find_hesitations: markiert die Zeitpunkte von "äh", "um" und Wiederholungen

Verändert die Datenbank NIE. Zurück kommen ausgabefertige Segmente
im Format, das backend/core/otio_export.py erwartet.

Format segment :
    {
        "clip_path": str,   # absoluter Pfad zur .mp4
        "clip_name": str,   # nom lisible
        "media_start": float,  # Versatz im Ausgangsclip, in Sekunden
        "duration": float,     # Dauer des Segments, in Sekunden
        "src_scene_id": str,   # Herkunft, zur Nachvollziehbarkeit
    }
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("cinassist.cleanup")

# Merkmale des Zögerns, in drei Sprachen
HESITATION_PATTERNS = [
    r"\b(euh+|heu+|hein|voilà|donc|bon|alors|en fait|je veux dire|tu vois)\b",
    r"\b(ähm+|äh+|halt|also|quasi|irgendwie|sozusagen)\b",
    r"\b(um+|uh+|er+|like|you know|i mean|actually|basically|sort of|kind of)\b",
]
_HESIT_RE = re.compile("|".join(HESITATION_PATTERNS), re.IGNORECASE)


def _scene_speech_segments(scene: dict) -> list[dict]:
    """Liest die Whisper-Segmente einer Szene. Format {start, end, text}."""
    raw = scene.get("transkription_json")
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = raw.get("segmente") or raw.get("segments") or []
    segs = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        start = float(s.get("start", 0.0))
        end = float(s.get("end", 0.0))
        text = str(s.get("text", "")).strip()
        if end > start:
            segs.append({"start": start, "end": end, "text": text})
    return sorted(segs, key=lambda x: x["start"])


def remove_silences_from_scenes(
    scenes: list[dict],
    min_silence_ms: int = 800,
    keep_margin_ms: int = 150,
) -> dict:
    """
    Zerlegt jede Szene in Abschnitte, die den gesprochenen Stellen entsprechen.

    Args:
        scenes: Liste von Szenen-Wörterbüchern mit mindestens
                {id, start_zeit, end_zeit, transkription_json,
                 clip: {dateipfad, dateiname}}
        min_silence_ms: Mindestdauer einer Stille, damit geschnitten wird, voreingestellt 800 ms.
        keep_margin_ms: Puffer vor und nach jedem gesprochenen Abschnitt, voreingestellt 150 ms.

    Returns:
        {
            "segments": [...],       # segments finaux export-ready
            "original_duration_s": float,
            "cleaned_duration_s": float,
            "silence_removed_s": float,
            "scenes_processed": int,
            "scenes_skipped_no_speech": int,
        }
    """
    min_gap = min_silence_ms / 1000.0
    margin = keep_margin_ms / 1000.0

    all_segments: list[dict] = []
    all_silences: list[dict] = []  # entfernte Bereiche, zur Anzeige in der Zeitleiste
    total_original = 0.0
    scenes_no_speech = 0

    for scene in scenes:
        scene_start = float(scene["start_zeit"])
        scene_end = float(scene["end_zeit"])
        scene_dur = scene_end - scene_start
        total_original += scene_dur

        speech = _scene_speech_segments(scene)
        clip = scene.get("clip") or {}
        clip_path = clip.get("dateipfad") or clip.get("clip_path")
        clip_name = clip.get("dateiname") or clip.get("clip_name") or "unknown"
        scene_id = scene.get("id") or scene.get("scene_id") or ""

        if not speech or not clip_path:
            # Ohne Sprache wird entweder unverändert behalten oder übersprungen
            # Vorgabe: überspringen, also strenges Sichten
            scenes_no_speech += 1
            continue

        # Nah beieinander liegende Segmente zusammenfassen, Abstand kleiner als min_gap
        merged: list[dict] = []
        for seg in speech:
            # Whisper-Zeiten sind ABSOLUT im Clip, nicht relativ zur Szene
            # Bisweilen sind sie relativ; angenommen wird absolut, wie bei Whisper üblich
            if not merged or seg["start"] - merged[-1]["end"] > min_gap:
                merged.append({"start": seg["start"], "end": seg["end"]})
            else:
                merged[-1]["end"] = seg["end"]

        # Puffer um jeden zusammengefassten Bereich legen
        kept_spans: list[dict] = []
        for m in merged:
            m_start = max(scene_start, m["start"] - margin)
            m_end = min(scene_end, m["end"] + margin)
            if m_end <= m_start:
                continue
            kept_spans.append({"start": m_start, "end": m_end})
            all_segments.append({
                "clip_path": clip_path,
                "clip_name": clip_name,
                "media_start": round(m_start, 3),
                "duration": round(m_end - m_start, 3),
                "src_scene_id": str(scene_id),
                "src_type": "speech",
            })

        # Stille ist das Gegenstück zu den behaltenen Abschnitten in [scene_start, scene_end].
        # Die Oberfläche zeichnet daraus die Vorschau der zu löschenden Bereiche auf der
        # Zeitleiste zur Anzeige. Zu beachten: die kept_spans können sich überlappen
        # nach Anwendung des Sicherheitsabstands; zusammengeführt wird vor der Berechnung.
        if kept_spans:
            kept_sorted = sorted(kept_spans, key=lambda x: x["start"])
            fused: list[dict] = [kept_sorted[0].copy()]
            for span in kept_sorted[1:]:
                if span["start"] <= fused[-1]["end"]:
                    fused[-1]["end"] = max(fused[-1]["end"], span["end"])
                else:
                    fused.append(span.copy())

            def _add_silence(start: float, end: float) -> None:
                if end - start < 0.05:
                    return
                all_silences.append({
                    "clip_path": clip_path,
                    "clip_name": clip_name,
                    "media_start": round(start, 3),
                    "duration": round(end - start, 3),
                    "src_scene_id": str(scene_id),
                })

            _add_silence(scene_start, fused[0]["start"])
            for i in range(len(fused) - 1):
                _add_silence(fused[i]["end"], fused[i + 1]["start"])
            _add_silence(fused[-1]["end"], scene_end)

    cleaned_dur = sum(s["duration"] for s in all_segments)
    return {
        "segments": all_segments,
        "silences": all_silences,
        "original_duration_s": round(total_original, 2),
        "cleaned_duration_s": round(cleaned_dur, 2),
        "silence_removed_s": round(total_original - cleaned_dur, 2),
        "scenes_processed": len(scenes) - scenes_no_speech,
        "scenes_skipped_no_speech": scenes_no_speech,
    }


def find_hesitations_in_scenes(scenes: list[dict]) -> dict:
    """
    Sucht Zögerungsmarker (ähm, um, euh, hein, unmittelbare Wiederholungen).

    Gibt Timestamps + Cut-Vorschlag pro Szene zurück. Verändert nichts.
    """
    hits: list[dict] = []
    total_hesitation_time = 0.0
    for scene in scenes:
        speech = _scene_speech_segments(scene)
        scene_id = str(scene.get("id") or scene.get("scene_id") or "")
        clip = scene.get("clip") or {}
        clip_name = clip.get("dateiname") or ""

        prev_word_norm = None
        for seg in speech:
            text = seg["text"]
            # Merkmale
            for m in _HESIT_RE.finditer(text):
                dur = seg["end"] - seg["start"]
                hits.append({
                    "scene_id": scene_id,
                    "clip_name": clip_name,
                    "start": round(seg["start"], 3),
                    "end": round(seg["end"], 3),
                    "matched": m.group(0),
                    "type": "hesitation_marker",
                    "context": text[:80],
                })
                total_hesitation_time += dur
            # Unmittelbare Wortwiederholungen (word_i == word_i+1)
            words = [w.strip(".,!?;:\"'").lower() for w in text.split() if w.strip()]
            for j in range(1, len(words)):
                if words[j] == words[j - 1] and len(words[j]) > 2:
                    hits.append({
                        "scene_id": scene_id,
                        "clip_name": clip_name,
                        "start": round(seg["start"], 3),
                        "end": round(seg["end"], 3),
                        "matched": words[j],
                        "type": "stutter_repetition",
                        "context": text[:80],
                    })

    return {
        "hesitation_count": len(hits),
        "estimated_time_savings_s": round(total_hesitation_time, 2),
        "hits": hits,
    }
