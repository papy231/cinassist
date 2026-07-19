"""
CinAssist — Cleanup automatique (Vague 2).

Depuis les timestamps de parole (Whisper transkription_json), produit :
    1. remove_silences : sous-segments de parole seulement (gaps > seuil coupés)
    2. find_hesitations : marque les timestamps de "euh"/"um"/répétitions

Ne modifie JAMAIS la DB. Retourne des "segments" prêts pour export
(compatibles avec le format attendu par backend/core/otio_export.py).

Format segment :
    {
        "clip_path": str,   # chemin absolu du .mp4
        "clip_name": str,   # nom lisible
        "media_start": float,  # offset dans le clip source (secondes)
        "duration": float,     # durée du segment (secondes)
        "src_scene_id": str,   # provenance (pour trace)
    }
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("cinassist.cleanup")

# Marqueurs d'hésitation (FR + DE + EN)
HESITATION_PATTERNS = [
    r"\b(euh+|heu+|hein|voilà|donc|bon|alors|en fait|je veux dire|tu vois)\b",
    r"\b(ähm+|äh+|halt|also|quasi|irgendwie|sozusagen)\b",
    r"\b(um+|uh+|er+|like|you know|i mean|actually|basically|sort of|kind of)\b",
]
_HESIT_RE = re.compile("|".join(HESITATION_PATTERNS), re.IGNORECASE)


def _scene_speech_segments(scene: dict) -> list[dict]:
    """Extrait les segments Whisper d'une scène. Format {start, end, text}."""
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
    Découpe chaque scène en sous-segments correspondant aux moments de parole.

    Args:
        scenes: liste de dicts scène avec au minimum
                {id, start_zeit, end_zeit, transkription_json,
                 clip: {dateipfad, dateiname}}
        min_silence_ms: durée minimale d'un silence pour être coupé (défaut 800ms).
        keep_margin_ms: marge à conserver avant/après chaque segment parlé (défaut 150ms).

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
    all_silences: list[dict] = []  # intervalles retirés (pour visualisation timeline)
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
            # Pas de parole → soit on garde tel quel, soit on skip
            # Politique par défaut : skip (dérushage strict)
            scenes_no_speech += 1
            continue

        # Fusionner segments proches (< min_gap entre eux)
        merged: list[dict] = []
        for seg in speech:
            # Timestamps Whisper = ABSOLUS dans le clip, pas relatifs à la scène
            # Mais parfois relatifs — on suppose absolus (Whisper standard)
            if not merged or seg["start"] - merged[-1]["end"] > min_gap:
                merged.append({"start": seg["start"], "end": seg["end"]})
            else:
                merged[-1]["end"] = seg["end"]

        # Ajouter marge autour de chaque merged span
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

        # Silences = complément des kept_spans dans [scene_start, scene_end].
        # Utilisé côté frontend pour dessiner les fantômes deleteRange sur la
        # timeline (visualisation HITL). NB : les kept_spans peuvent se recouvrir
        # après application de la margin — on fusionne avant de calculer.
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
            # Marqueurs
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
            # Répétitions immédiates (word_i == word_i+1)
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
