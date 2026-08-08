# Benchmark timeline-from-prompt — 2026-07-25_165410
**Tag** : `full_v2`

**Prompts exécutés** : 16
**Modes assembler** : heuristic

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic | 16 | 0.510 | 1.000 | 1.000 | 1.000 | 0.209 | 0.663 | 0.694 | 0.337 | 0.124 | 78.899 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 5 | 0.504 | 1.000 | — | — | 0.108 | 0.810 | 0.474 | 0.404 | 0.033 | 68.874 |
| edge | 3 | 0.508 | 1.000 | 1.000 | — | 0.257 | 0.700 | 0.528 | 0.315 | 0.167 | 67.617 |
| interview | 5 | 0.554 | 1.000 | 1.000 | 1.000 | 0.169 | 0.500 | 0.550 | 0.271 | 0.213 | 95.242 |
| narrative_mixed | 3 | 0.451 | 1.000 | 1.000 | 1.000 | 0.396 | 0.653 | 1.466 | 0.359 | 0.083 | 79.653 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_short | heuristic | broll_nature | 0.465 | 1.000 | — | — | 0.275 | 4 | 0.000 | 0.438 | 73.640 |
| broll_nature_60s | heuristic | broll_nature | 0.507 | 1.000 | — | — | 0.051 | 8 | 0.000 | 0.386 | 96.910 |
| broll_urban | heuristic | broll_nature | 0.538 | 1.000 | — | — | 0.067 | 4 | 0.000 | 0.476 | 51.880 |
| broll_water_calm | heuristic | broll_nature | 0.497 | 1.000 | — | — | 0.090 | 4 | 0.000 | 0.399 | 47.910 |
| broll_energy_dynamic | heuristic | broll_nature | 0.511 | 1.000 | — | — | 0.060 | 5 | 0.167 | 0.319 | 74.030 |
| interview_40s_closeup | heuristic | interview | 0.557 | 1.000 | 1.000 | 1.000 | 0.013 | 6 | 0.000 | 0.215 | 74.560 |
| interview_60s_serious | heuristic | interview | 0.557 | 1.000 | 1.000 | 1.000 | 0.570 | 4 | 0.733 | 0.301 | 186.610 |
| interview_broll_mix | heuristic | interview | 0.545 | 1.000 | 1.000 | 1.000 | 0.040 | 8 | 0.000 | 0.293 | 93.720 |
| interview_short | heuristic | interview | 0.635 | 1.000 | 1.000 | 1.000 | 0.000 | 1 | 0.000 | 0.280 | 18.010 |
| interview_multi_speaker | heuristic | interview | 0.477 | 1.000 | 1.000 | 1.000 | 0.221 | 6 | 0.333 | 0.265 | 103.310 |
| narrative_documentary_open | heuristic | narrative_mixed | 0.396 | 1.000 | 1.000 | — | 0.422 | 3 | 0.000 | 0.334 | 44.480 |
| narrative_music_video | heuristic | narrative_mixed | 0.520 | 1.000 | — | — | 0.272 | 6 | 0.250 | 0.375 | 102.340 |
| narrative_teaser_energetic | heuristic | narrative_mixed | 0.437 | 1.000 | 1.000 | 1.000 | 0.494 | 8 | 0.000 | 0.367 | 92.140 |
| edge_very_short | heuristic | edge | 0.538 | 1.000 | — | — | 0.125 | 3 | 0.000 | 0.299 | 46.650 |
| edge_no_person_hard | heuristic | edge | 0.435 | 1.000 | — | — | 0.086 | 5 | 0.000 | 0.395 | 66.160 |
| edge_all_closeup | heuristic | edge | 0.550 | 1.000 | 1.000 | — | 0.560 | 4 | 0.500 | 0.251 | 90.040 |
