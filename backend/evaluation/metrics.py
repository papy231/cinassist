"""Quantitative Maße für die Auswertung der Timeline-Erzeugung.

Alle Maße sind reine Funktionen: Sie nehmen die Ergebnisse der Phasen
(plan, candidates, timeline) sowie pool_summary entgegen und geben ein
Wörterbuch mit Werten zurück. Kein Datei- oder Netzzugriff.

Verwendet von backend/evaluation/run_benchmark.py.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np


# ─── Semantische Abdeckung (CLIP Text gegen Text) ────────────────────────────

def coverage_score(plan: dict, candidates: dict, timeline: dict,
                   embed_fn) -> dict[str, float]:
    """Mittlere CLIP-Ähnlichkeit zwischen der Absichtsformulierung des Slots
    und der erzeugten Beschreibung der gewählten Szene. Höher bedeutet
    bessere inhaltliche Übereinstimmung.

    Gibt zusätzlich den Wert je Slot für die genauere Auswertung zurück.
    """
    slots_by_id = {str(s.get("slot_id")): s for s in (plan.get("slots") or [])}
    picked = [d for d in (timeline.get("decisions") or [])
              if d.get("outcome") == "picked"]

    scene_desc: dict[str, str] = {}
    for cand_list in (candidates.get("slots") or {}).values():
        for c in cand_list:
            scene_desc[str(c["scene_id"])] = c.get("beschreibung") or ""

    per_slot: list[dict] = []
    scores: list[float] = []
    for dec in picked:
        slot = slots_by_id.get(str(dec.get("slot_id")))
        if not slot:
            continue
        intent = (slot.get("intent_en") or slot.get("intent_de") or "").strip()
        desc = (scene_desc.get(str(dec.get("scene_id"))) or "").strip()
        if not intent or not desc:
            per_slot.append({"slot_id": str(dec.get("slot_id")), "score": None})
            continue
        try:
            e_i = embed_fn(intent)
            e_d = embed_fn(desc)
            na, nb = float(np.linalg.norm(e_i)), float(np.linalg.norm(e_d))
            sim = float(np.dot(e_i, e_d) / (na * nb)) if na and nb else 0.0
        except Exception:
            sim = 0.0
        scores.append(sim)
        per_slot.append({"slot_id": str(dec.get("slot_id")), "score": round(sim, 4)})

    return {
        "coverage_mean": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "coverage_min": round(min(scores), 4) if scores else 0.0,
        "coverage_max": round(max(scores), 4) if scores else 0.0,
        "coverage_per_slot": per_slot,
    }


# ─── Bedingungstreue (Einstellungsgröße, Sprecher, Dialog) ───────────────────

def constraint_precision(plan: dict, candidates: dict, timeline: dict,
                          speaker_map: dict[str, float]) -> dict[str, Any]:
    """Anteil der Slots, bei denen die gewählte Szene die Bedingungen des Plans
    erfüllt. framing_hint="any" gilt immer als erfüllt.
    """
    slots_by_id = {str(s.get("slot_id")): s for s in (plan.get("slots") or [])}
    scene_meta: dict[str, dict] = {}
    for cand_list in (candidates.get("slots") or {}).values():
        for c in cand_list:
            scene_meta[str(c["scene_id"])] = c

    picked = [d for d in (timeline.get("decisions") or [])
              if d.get("outcome") == "picked"]
    framing_hits = 0
    speaker_hits = 0
    speaker_total = 0
    dialogue_hits = 0
    dialogue_total = 0

    for dec in picked:
        slot = slots_by_id.get(str(dec.get("slot_id")))
        scene = scene_meta.get(str(dec.get("scene_id")))
        if not slot or not scene:
            continue
        # framing
        fh = slot.get("framing_hint", "any")
        if fh == "any" or fh == scene.get("framing"):
            framing_hits += 1
        # speaker
        if slot.get("needs_speaker"):
            speaker_total += 1
            if speaker_map.get(str(scene["scene_id"]), 0.0) >= 0.5:
                speaker_hits += 1
        # dialogue
        if slot.get("needs_dialogue"):
            dialogue_total += 1
            trans = scene.get("transkription") or ""
            if trans and len(trans.strip()) >= 20:
                dialogue_hits += 1

    n = len(picked)
    return {
        "framing_precision": round(framing_hits / n, 4) if n else 1.0,
        "speaker_precision": round(speaker_hits / speaker_total, 4) if speaker_total else None,
        "dialogue_precision": round(dialogue_hits / dialogue_total, 4) if dialogue_total else None,
        "n_speaker_constrained": speaker_total,
        "n_dialogue_constrained": dialogue_total,
    }


# ─── Laufzeit, Dauer, Struktur ───────────────────────────────────────────────

def duration_metrics(plan: dict, timeline: dict, target_duration_s: float) -> dict[str, float]:
    total = float(timeline.get("_meta", {}).get("total_duration_s") or 0.0)
    deviation_abs = abs(total - target_duration_s)
    deviation_pct = deviation_abs / max(target_duration_s, 1e-6)
    return {
        "target_duration_s": round(target_duration_s, 2),
        "actual_duration_s": round(total, 2),
        "duration_deviation_s": round(deviation_abs, 2),
        "duration_deviation_pct": round(deviation_pct, 4),
    }


def diversity_metrics(timeline: dict) -> dict[str, float]:
    segs = timeline.get("segments") or []
    picked_decisions = [d for d in (timeline.get("decisions") or [])
                        if d.get("outcome") == "picked"]
    n_segs = len(segs)
    if n_segs == 0:
        return {"n_segments": 0, "clip_diversity": 0.0,
                "framing_entropy": 0.0, "unique_clips": 0}

    clips = [s.get("clip_name") for s in segs]
    unique_clips = len(set(clips))
    clip_diversity = unique_clips / n_segs

    framings = [d.get("framing") for d in picked_decisions if d.get("framing")]
    fc = Counter(framings)
    total = sum(fc.values())
    if total > 0:
        probs = [v / total for v in fc.values()]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    else:
        entropy = 0.0

    return {
        "n_segments": n_segs,
        "unique_clips": unique_clips,
        "clip_diversity": round(clip_diversity, 4),
        "framing_distribution": dict(fc),
        "framing_entropy": round(entropy, 4),
    }


def score_metrics(candidates: dict, timeline: dict) -> dict[str, float]:
    """Mittlerer Rangwert der gewählten Kandidaten und Abstand zum Zweitbesten je Slot."""
    picked = [d for d in (timeline.get("decisions") or [])
              if d.get("outcome") == "picked"]
    top1_scores = [float(d.get("score") or d.get("clip_score") or 0.0)
                   for d in picked]

    gaps: list[float] = []
    slots_c = candidates.get("slots") or {}
    for sid, cand_list in slots_c.items():
        if len(cand_list) >= 2:
            gaps.append(float(cand_list[0].get("score") or cand_list[0].get("clip_score") or 0.0)
                        - float(cand_list[1].get("score") or cand_list[1].get("clip_score") or 0.0))

    return {
        "avg_top1_score": round(sum(top1_scores) / len(top1_scores), 4) if top1_scores else 0.0,
        "min_top1_score": round(min(top1_scores), 4) if top1_scores else 0.0,
        "max_top1_score": round(max(top1_scores), 4) if top1_scores else 0.0,
        "avg_top1_top2_gap": round(sum(gaps) / len(gaps), 4) if gaps else 0.0,
    }


def structural_metrics(plan: dict, timeline: dict) -> dict[str, Any]:
    slots = plan.get("slots") or []
    picked = [d for d in (timeline.get("decisions") or [])
              if d.get("outcome") == "picked"]
    skipped = [d for d in (timeline.get("decisions") or [])
               if d.get("outcome") != "picked"]
    trim_strats = Counter(d.get("trim_strategy") for d in picked if d.get("trim_strategy"))
    return {
        "n_slots_planned": len(slots),
        "n_slots_picked": len(picked),
        "n_slots_skipped": len(skipped),
        "skipped_ratio": round(len(skipped) / len(slots), 4) if slots else 0.0,
        "trim_strategy_distribution": dict(trim_strats),
    }


def timing_metrics(pool_summary_wall: float, plan: dict, candidates: dict,
                   timeline: dict, benchmark_wall_s: float) -> dict[str, float]:
    return {
        "wall_total_s": round(benchmark_wall_s, 2),
        "wall_pool_summary_s": round(pool_summary_wall, 2),
        "wall_plan_s": float(plan.get("_meta", {}).get("wall_s") or 0.0),
        "wall_retrieve_s": float(candidates.get("_meta", {}).get("wall_s") or 0.0),
        "wall_assemble_s": float(timeline.get("_meta", {}).get("wall_s") or 0.0),
    }


# ─── Facade : compute all metrics ────────────────────────────────────────────

def compute_all(plan: dict, candidates: dict, timeline: dict,
                target_duration_s: float, pool_summary: dict,
                pool_summary_wall_s: float, benchmark_wall_s: float,
                embed_fn, speaker_map: dict[str, float]) -> dict[str, Any]:
    """Berechnet alle Maße und gibt sie als flaches Wörterbuch zurück,
    verwendbar für pandas und CSV."""
    out: dict[str, Any] = {}
    out.update(duration_metrics(plan, timeline, target_duration_s))
    out.update(diversity_metrics(timeline))
    out.update(score_metrics(candidates, timeline))
    out.update(structural_metrics(plan, timeline))
    out.update(constraint_precision(plan, candidates, timeline, speaker_map))
    out.update(coverage_score(plan, candidates, timeline, embed_fn))
    out.update(timing_metrics(pool_summary_wall_s, plan, candidates, timeline,
                              benchmark_wall_s))
    out["pool_size"] = pool_summary.get("total_scenes", 0)
    return out


def flatten_for_csv(metrics: dict) -> dict[str, Any]:
    """Nimmt ein gemischtes Maß-Wörterbuch (mit Unterlisten und Unterwörterbüchern) und gibt ein
    flaches Wörterbuch im CSV-Format. Verschachtelte Felder bleiben außen vor."""
    out: dict[str, Any] = {}
    skip_keys = {"coverage_per_slot", "framing_distribution",
                 "trim_strategy_distribution"}
    for k, v in metrics.items():
        if k in skip_keys:
            continue
        if isinstance(v, (dict, list)):
            continue
        out[k] = v
    return out
