"""CLI de test end-to-end pour le générateur timeline-from-prompt.

Enchaîne Phase 1 (plan_timeline) + Phase 2 (retrieve_candidates) sur la DB réelle
et écrit tous les logs sous outputs/timeline_gen_logs/{run_id}/.

Usage:
    python -m backend.tools.test_timeline_generator \\
        --prompt "90s über die Einsamkeit des Kochs" --duration 90

Par défaut utilise TOUS les clips analysés. Pour restreindre :
    --clip-ids uuid1,uuid2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal, Clip
from backend.core.timeline_generator import (
    _log_stage,
    assemble_timeline,
    plan_timeline,
    retrieve_candidates,
    summarize_pool,
)


async def _get_project_clip_ids(explicit: list[str] | None) -> list[str]:
    async with AsyncSessionLocal() as db:
        if explicit:
            r = await db.execute(select(Clip.id).where(Clip.id.in_(explicit)))
        else:
            r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
        return [str(cid) for (cid,) in r.all()]


async def run(prompt: str, duration_s: float, num_slots_hint: int | None,
              clip_ids: list[str] | None, top_k: int, dedupe: bool,
              assemble_mode: str) -> int:
    run_id = f"run_{int(time.time())}"
    print(f"→ run_id: {run_id}", file=sys.stderr)

    project_ids = await _get_project_clip_ids(clip_ids)
    if not project_ids:
        print("ERROR: aucun clip analysé dans la DB", file=sys.stderr)
        return 2
    print(f"→ pool: {len(project_ids)} clips analysés", file=sys.stderr)

    # Phase 0 : pool summary
    print("\n=== PHASE 0 : POOL SUMMARY ===", file=sys.stderr)
    async with AsyncSessionLocal() as db:
        pool_summary = await summarize_pool(db, project_ids)
    _log_stage("00_pool_summary", pool_summary, run_id)
    print(f"→ {pool_summary.get('total_scenes')} scènes, framings={pool_summary.get('framing_counts')}, "
          f"dialog={pool_summary.get('scenes_with_dialogue')}, "
          f"speakers={len(pool_summary.get('speakers') or [])}",
          file=sys.stderr)

    # Phase 1
    print("\n=== PHASE 1 : PLAN (pool-aware) ===", file=sys.stderr)
    plan = await plan_timeline(prompt, duration_s, num_slots_hint,
                               pool_summary=pool_summary)
    _log_stage("01_plan", plan, run_id)
    warns = plan.get("_validation_warnings") or []
    slots = plan.get("slots") or []
    print(f"→ {len(slots)} slots, wall={plan['_meta']['wall_s']}s", file=sys.stderr)
    if warns:
        print(f"⚠️  warnings: {warns}", file=sys.stderr)
    print(f"→ narrative: {plan.get('narrative_intent_de')!r}", file=sys.stderr)

    # Phase 2
    print("\n=== PHASE 2 : RETRIEVE ===", file=sys.stderr)
    async with AsyncSessionLocal() as db:
        candidates = await retrieve_candidates(
            plan, project_ids, db, top_k=top_k, dedupe_across_slots=dedupe
        )
    _log_stage("02_candidates", candidates, run_id)
    slots_c = candidates.get("slots") or {}
    print(f"→ pool_size={candidates['_meta']['pool_size']}, "
          f"wall={candidates['_meta']['wall_s']}s", file=sys.stderr)

    # Résumé lisible
    print("\n=== ZUSAMMENFASSUNG PRO SLOT ===", file=sys.stderr)
    empty_slots = 0
    for slot in slots:
        sid = str(slot["slot_id"])
        cands = slots_c.get(sid, [])
        marker = "✅" if cands else "❌"
        print(f"  {marker} Slot {sid} [{slot['framing_hint']}] "
              f"{slot['intent_de'][:60]}", file=sys.stderr)
        if not cands:
            empty_slots += 1
            continue
        for c in cands[:3]:
            print(f"       → {c.get('score', c['clip_score']):.3f} "
                  f"(clip={c['clip_score']:.3f} txt={c.get('text_score', 0):.3f}) · "
                  f"scene {c['szenen_nr']} of {c['clip_name']} · "
                  f"dauer={c['dauer']:.1f}s · {c['framing']}", file=sys.stderr)
    if empty_slots:
        print(f"\n⚠️  {empty_slots}/{len(slots)} slots sans candidat", file=sys.stderr)

    # Phase 3
    print("\n=== PHASE 3 : ASSEMBLE ===", file=sys.stderr)
    timeline = await assemble_timeline(plan, candidates, mode=assemble_mode)
    _log_stage("03_timeline", timeline, run_id)
    meta = timeline["_meta"]
    print(f"→ mode={meta['mode']}, segments={meta['segment_count']}, "
          f"total_dur={meta['total_duration_s']}s (target {duration_s}s), "
          f"skipped={meta['skipped_slots']}, wall={meta['wall_s']}s",
          file=sys.stderr)
    for seg, dec in zip(timeline["segments"], [d for d in timeline["decisions"] if d.get("outcome") == "picked"]):
        print(f"    · {seg['clip_name']} @ {seg['media_start']:.1f}s "
              f"({seg['duration']:.1f}s) [trim={dec.get('trim_strategy')}]",
              file=sys.stderr)

    print(f"\n→ logs: backend/outputs/timeline_gen_logs/{run_id}/", file=sys.stderr)
    # stdout minimal pour scripting
    print(json.dumps({
        "run_id": run_id,
        "slots": len(slots),
        "empty_slots": empty_slots,
        "segments": meta["segment_count"],
        "total_duration_s": meta["total_duration_s"],
    }))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--num-slots-hint", type=int, default=None)
    ap.add_argument("--clip-ids", type=str, default=None,
                    help="Comma-separated clip UUIDs (default = all analyzed clips)")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--no-dedupe", action="store_true",
                    help="Autorise qu'une scène apparaisse dans plusieurs slots")
    ap.add_argument("--assemble-mode", choices=("heuristic", "llm"),
                    default="heuristic",
                    help="heuristic = top-1 + centre (rapide), llm = qwen picke")
    args = ap.parse_args()

    clip_ids = None
    if args.clip_ids:
        clip_ids = [x.strip() for x in args.clip_ids.split(",") if x.strip()]

    return asyncio.run(run(
        args.prompt, args.duration, args.num_slots_hint, clip_ids,
        args.top_k, not args.no_dedupe, args.assemble_mode,
    ))


if __name__ == "__main__":
    sys.exit(main())
