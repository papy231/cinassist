# Benchmark timeline-from-prompt — 2026-07-26_163757
**Tag** : `smoke_baseline2`

**Prompts exécutés** : 2
**Modes assembler** : baseline_no_filter, baseline_single_shot

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_no_filter | 1 | 0.592 | 1.000 | — | — | 0.764 | 0.667 | 0.000 | 0.525 | 0.000 | 70.050 |
| baseline_single_shot | 1 | 0.000 | 1.000 | — | — | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 96.510 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 2 | 0.296 | 1.000 | — | — | 0.882 | 0.333 | 0.000 | 0.262 | 0.500 | 83.280 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_short | baseline_no_filter | broll_nature | 0.592 | 1.000 | — | — | 0.764 | 3 | 0.000 | 0.525 | 70.050 |
| broll_nature_short | baseline_single_shot | broll_nature | 0.000 | 1.000 | — | — | 1.000 | 0 | 1.000 | 0.000 | 96.510 |
