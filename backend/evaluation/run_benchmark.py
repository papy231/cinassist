"""Runner de benchmark quantitatif pour le générateur timeline-from-prompt.

Itère sur un set de prompts, exécute Plan → Retrieve → Assemble, calcule
toutes les métriques (backend/evaluation/metrics.py) et écrit un rapport dans
`backend/evaluation/reports/{run_ts}/` :

- `results.json` — dict complet par prompt (input pour re-analyse)
- `results.csv` — 1 ligne / prompt, colonnes = métriques flat
- `summary.md` — tableau global + par-profil, lisible humain

Usage :
    python -m backend.evaluation.run_benchmark
    python -m backend.evaluation.run_benchmark --profile broll_nature
    python -m backend.evaluation.run_benchmark --assemble-mode llm --limit 5
    python -m backend.evaluation.run_benchmark --tag improvements_v2
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path
from statistics import mean, median

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal, Clip, SceneSpeaker
from backend.core.timeline_generator import (
    _log_stage,
    assemble_timeline,
    plan_timeline,
    retrieve_candidates,
    summarize_pool,
)
from backend.evaluation import metrics as M
from backend.evaluation.baselines import BASELINE_MODES, run_baseline
from backend.evaluation.prompts_benchmark import (
    ALL_PROMPTS,
    BY_PROFILE,
    BenchmarkPrompt,
)


PIPELINE_MODES = {"heuristic", "llm"}
ALL_MODES = PIPELINE_MODES | BASELINE_MODES


REPORT_ROOT = Path(__file__).resolve().parent / "reports"


# ─── Helpers DB ──────────────────────────────────────────────────────────────

async def _get_analyzed_clip_ids(db) -> list[str]:
    r = await db.execute(select(Clip.id).where(Clip.status == "analysiert"))
    return [str(cid) for (cid,) in r.all()]


async def _load_speaker_map(db, clip_ids: list[str]) -> dict[str, float]:
    """Retourne {scene_id: max speaking_time} pour toutes les scènes du pool.
    Utilisé par constraint_precision.speaker_precision."""
    if not clip_ids:
        return {}
    from backend.core.database import Szene
    scene_ids_stmt = select(Szene.id).where(Szene.clip_id.in_(clip_ids))
    scene_ids = [str(sid) for (sid,) in (await db.execute(scene_ids_stmt)).all()]
    if not scene_ids:
        return {}
    stmt = select(SceneSpeaker.scene_id, SceneSpeaker.speaking_time).where(
        SceneSpeaker.scene_id.in_(scene_ids)
    )
    rows = (await db.execute(stmt)).all()
    out: dict[str, float] = {}
    for sid, st in rows:
        key = str(sid)
        out[key] = max(out.get(key, 0.0), float(st or 0.0))
    return out


def _embed_fn():
    """Retourne une fonction embed_text (CLIP text encoder singleton)."""
    from backend.core.timeline_generator import _embed_text_lazy
    return _embed_text_lazy


# ─── Exécution d'un prompt ───────────────────────────────────────────────────

async def run_one(bp: BenchmarkPrompt, clip_ids: list[str],
                  assemble_mode: str, top_k: int,
                  speaker_map: dict[str, float],
                  use_query_rewrite: bool = False) -> dict:
    """Exécute les 3 phases (ou une baseline) + calcule les métriques pour UN prompt."""
    t_start = time.time()

    if assemble_mode in BASELINE_MODES:
        baseline_result = await run_baseline(assemble_mode, bp.prompt,
                                              bp.duration_s, clip_ids)
        plan = baseline_result["plan"]
        candidates = baseline_result["candidates"]
        timeline = baseline_result["timeline"]
        pool_summary = baseline_result["pool_summary"]
        pool_wall = baseline_result["pool_summary_wall_s"]
    else:
        # Phase 0
        t0 = time.time()
        async with AsyncSessionLocal() as db:
            pool_summary = await summarize_pool(db, clip_ids)
        pool_wall = time.time() - t0

        # Phase 1
        plan = await plan_timeline(bp.prompt, bp.duration_s,
                                   num_slots_hint=None,
                                   pool_summary=pool_summary)

        # Phase 2
        async with AsyncSessionLocal() as db:
            candidates = await retrieve_candidates(plan, clip_ids, db, top_k=top_k,
                                                   use_query_rewrite=use_query_rewrite)

        # Phase 3
        timeline = await assemble_timeline(plan, candidates, mode=assemble_mode,
                                           target_duration_s=bp.duration_s)

    bench_wall = time.time() - t_start

    # Metrics
    scoring = M.compute_all(
        plan=plan,
        candidates=candidates,
        timeline=timeline,
        target_duration_s=bp.duration_s,
        pool_summary=pool_summary,
        pool_summary_wall_s=pool_wall,
        benchmark_wall_s=bench_wall,
        embed_fn=_embed_fn(),
        speaker_map=speaker_map,
    )
    scoring["prompt_id"] = bp.id
    scoring["profile"] = bp.profile
    scoring["assemble_mode"] = assemble_mode
    scoring["prompt_text"] = bp.prompt
    return {
        "meta": {
            "prompt": bp.__dict__.copy(),
            "assemble_mode": assemble_mode,
            "top_k": top_k,
        },
        "metrics": scoring,
        "plan": plan,
        "timeline_summary": timeline.get("_meta"),
        "decisions": timeline.get("decisions"),
    }


# ─── Génération rapport ──────────────────────────────────────────────────────

def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _fmt(v, digits: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def build_summary_md(all_results: list[dict], run_ts: str, tag: str) -> str:
    lines: list[str] = []
    lines.append(f"# Benchmark timeline-from-prompt — {run_ts}")
    if tag:
        lines.append(f"**Tag** : `{tag}`")
    lines.append("")
    lines.append(f"**Prompts exécutés** : {len(all_results)}")
    if all_results:
        modes = sorted({r['metrics']['assemble_mode'] for r in all_results})
        lines.append(f"**Modes assembler** : {', '.join(modes)}")
    lines.append("")

    # ── Statistiques globales par mode ──
    lines.append("## Statistiques globales")
    lines.append("")
    keys = [
        "coverage_mean", "framing_precision", "speaker_precision",
        "dialogue_precision", "duration_deviation_pct",
        "clip_diversity", "framing_entropy", "avg_top1_score",
        "skipped_ratio", "wall_total_s",
    ]
    headers = ["mode", "n"] + keys
    lines.append(_md_row(headers))
    lines.append(_md_row(["---"] * len(headers)))
    from collections import defaultdict
    by_mode = defaultdict(list)
    for r in all_results:
        by_mode[r["metrics"]["assemble_mode"]].append(r["metrics"])
    for mode, rows in by_mode.items():
        vals = [mode, str(len(rows))]
        for k in keys:
            nums = [row.get(k) for row in rows if isinstance(row.get(k), (int, float))]
            vals.append(_fmt(mean(nums)) if nums else "—")
        lines.append(_md_row(vals))
    lines.append("")

    # ── Par profil ──
    lines.append("## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)")
    lines.append("")
    lines.append(_md_row(["profile", "n"] + keys))
    lines.append(_md_row(["---"] * (len(keys) + 2)))
    by_profile = defaultdict(list)
    prefer_mode = "heuristic" if any(r["metrics"]["assemble_mode"] == "heuristic" for r in all_results) else None
    for r in all_results:
        if prefer_mode and r["metrics"]["assemble_mode"] != prefer_mode:
            continue
        by_profile[r["metrics"]["profile"]].append(r["metrics"])
    for prof, rows in sorted(by_profile.items()):
        vals = [prof, str(len(rows))]
        for k in keys:
            nums = [row.get(k) for row in rows if isinstance(row.get(k), (int, float))]
            vals.append(_fmt(mean(nums)) if nums else "—")
        lines.append(_md_row(vals))
    lines.append("")

    # ── Détail par prompt ──
    lines.append("## Résultats par prompt")
    lines.append("")
    detail_keys = ["coverage_mean", "framing_precision", "speaker_precision",
                   "dialogue_precision", "duration_deviation_pct",
                   "n_segments", "skipped_ratio", "avg_top1_score",
                   "wall_total_s"]
    lines.append(_md_row(["prompt_id", "mode", "profile"] + detail_keys))
    lines.append(_md_row(["---"] * (len(detail_keys) + 3)))
    for r in all_results:
        m = r["metrics"]
        row = [m["prompt_id"], m["assemble_mode"], m["profile"]]
        for k in detail_keys:
            row.append(_fmt(m.get(k)))
        lines.append(_md_row(row))
    lines.append("")

    return "\n".join(lines)


def write_csv(all_results: list[dict], path: Path) -> None:
    if not all_results:
        return
    flat_rows = [M.flatten_for_csv(r["metrics"]) for r in all_results]
    # Union des clés (les rows peuvent avoir des champs différents)
    all_keys: list[str] = []
    for row in flat_rows:
        for k in row:
            if k not in all_keys:
                all_keys.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_keys)
        w.writeheader()
        for row in flat_rows:
            w.writerow(row)


# ─── Main ────────────────────────────────────────────────────────────────────

async def main_async(args) -> int:
    async with AsyncSessionLocal() as db:
        clip_ids = await _get_analyzed_clip_ids(db)
        if not clip_ids:
            print("ERROR: aucun clip analysé dans la DB", file=sys.stderr)
            return 2
        speaker_map = await _load_speaker_map(db, clip_ids)

    if args.profile:
        prompts = BY_PROFILE.get(args.profile, [])
    elif args.prompt_ids:
        wanted = set(x.strip() for x in args.prompt_ids.split(","))
        prompts = [p for p in ALL_PROMPTS if p.id in wanted]
    else:
        prompts = list(ALL_PROMPTS)

    if args.limit:
        prompts = prompts[:args.limit]

    if not prompts:
        print("ERROR: aucun prompt à exécuter", file=sys.stderr)
        return 2

    modes = [m.strip() for m in args.assemble_modes.split(",") if m.strip()]
    invalid = [m for m in modes if m not in ALL_MODES]
    if invalid:
        print(f"ERROR: unknown mode(s): {invalid}. Valid: {sorted(ALL_MODES)}",
              file=sys.stderr)
        return 2
    total_runs = len(prompts) * len(modes)
    print(f"→ Pool : {len(clip_ids)} clips analysés, speaker_map={len(speaker_map)} entries",
          file=sys.stderr)
    print(f"→ Prompts : {len(prompts)}, modes : {modes} → {total_runs} runs\n",
          file=sys.stderr)

    run_ts = time.strftime("%Y-%m-%d_%H%M%S")
    out_dir = REPORT_ROOT / f"benchmark_{run_ts}{('_' + args.tag) if args.tag else ''}"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    for i, bp in enumerate(prompts, 1):
        for mode in modes:
            label = f"[{i}/{len(prompts)}·{mode}] {bp.id}"
            print(f"▶ {label} (target {bp.duration_s:.0f}s)", file=sys.stderr)
            t0 = time.time()
            try:
                result = await run_one(bp, clip_ids, mode, args.top_k, speaker_map,
                                       use_query_rewrite=args.query_rewrite)
                m = result["metrics"]
                print(f"  ✅ cov={_fmt(m['coverage_mean'])} · "
                      f"framing_p={_fmt(m['framing_precision'])} · "
                      f"dur_dev={_fmt(m['duration_deviation_pct'])} · "
                      f"top1={_fmt(m['avg_top1_score'])} · "
                      f"wall={time.time()-t0:.1f}s", file=sys.stderr)
                all_results.append(result)
            except Exception as e:
                print(f"  ❌ {e}", file=sys.stderr)
                all_results.append({
                    "meta": {"prompt": bp.__dict__.copy(), "assemble_mode": mode},
                    "error": str(e),
                    "metrics": {"prompt_id": bp.id, "profile": bp.profile,
                                "assemble_mode": mode, "prompt_text": bp.prompt,
                                "error": str(e)},
                })

    # Persist
    (out_dir / "results.json").write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False, default=str)
    )
    write_csv([r for r in all_results if "error" not in r], out_dir / "results.csv")
    (out_dir / "summary.md").write_text(build_summary_md(
        [r for r in all_results if "error" not in r], run_ts, args.tag
    ))

    print(f"\n→ Rapport : {out_dir}/summary.md", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=list(BY_PROFILE.keys()), default=None,
                    help="Restreindre à un profil (broll_nature|interview|narrative_mixed|edge)")
    ap.add_argument("--prompt-ids", type=str, default=None,
                    help="IDs de prompts (comma-separated), ignore --profile")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limiter à N prompts (utile pour smoke-test)")
    ap.add_argument("--assemble-modes", type=str, default="heuristic",
                    help=("Modes à comparer, séparés par virgule. Pipeline: "
                          "'heuristic', 'llm'. Baselines: 'baseline_random', "
                          "'baseline_no_filter', 'baseline_single_shot'. "
                          "Ex: 'heuristic,baseline_random,baseline_single_shot'"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--query-rewrite", action="store_true",
                    help="Aktiviert llama3 Query-Rewriting vor CLIP-Embedding (Ablation).")
    ap.add_argument("--tag", type=str, default="",
                    help="Suffixe optionnel du dossier rapport (ex: 'improvements_v2')")
    args = ap.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
