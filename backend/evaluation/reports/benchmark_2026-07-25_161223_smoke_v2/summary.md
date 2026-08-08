# Benchmark timeline-from-prompt — 2026-07-25_161223
**Tag** : `smoke_v2`

**Prompts exécutés** : 3
**Modes assembler** : heuristic

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic | 3 | 0.480 | 1.000 | — | — | 0.258 | 0.824 | 0.000 | 0.396 | 0.000 | 107.767 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 3 | 0.480 | 1.000 | — | — | 0.258 | 0.824 | 0.000 | 0.396 | 0.000 | 107.767 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_short | heuristic | broll_nature | 0.445 | 1.000 | — | — | 0.089 | 3 | 0.000 | 0.394 | 45.110 |
| broll_nature_60s | heuristic | broll_nature | 0.486 | 1.000 | — | — | 0.681 | 17 | 0.000 | 0.350 | 216.420 |
| broll_urban | heuristic | broll_nature | 0.511 | 1.000 | — | — | 0.004 | 5 | 0.000 | 0.444 | 61.770 |
