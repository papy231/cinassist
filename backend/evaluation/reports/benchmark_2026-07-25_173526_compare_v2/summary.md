# Benchmark timeline-from-prompt — 2026-07-25_173526
**Tag** : `compare_v2`

**Prompts exécutés** : 14
**Modes assembler** : heuristic, llm

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic | 7 | 0.509 | 1.000 | 1.000 | 1.000 | 0.087 | 0.647 | 0.840 | 0.356 | 0.021 | 94.546 |
| llm | 7 | 0.517 | 1.000 | 1.000 | 1.000 | 0.300 | 0.629 | 0.882 | 0.337 | 0.043 | 172.234 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 2 | 0.514 | 1.000 | — | — | 0.007 | 0.838 | 0.000 | 0.370 | 0.000 | 81.640 |
| edge | 1 | 0.498 | 1.000 | — | — | 0.093 | 1.000 | 0.000 | 0.480 | 0.000 | 53.570 |
| interview | 2 | 0.560 | 1.000 | 1.000 | 1.000 | 0.046 | 0.417 | 1.250 | 0.288 | 0.000 | 83.505 |
| narrative_mixed | 2 | 0.457 | 1.000 | 1.000 | — | 0.206 | 0.510 | 1.689 | 0.346 | 0.075 | 138.980 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_60s | heuristic | broll_nature | 0.465 | 1.000 | — | — | 0.012 | 8 | 0.000 | 0.334 | 100.070 |
| broll_nature_60s | llm | broll_nature | 0.463 | 1.000 | — | — | 0.333 | 8 | 0.000 | 0.401 | 222.170 |
| broll_urban | heuristic | broll_nature | 0.563 | 1.000 | — | — | 0.001 | 5 | 0.000 | 0.407 | 63.210 |
| broll_urban | llm | broll_nature | 0.512 | 1.000 | — | — | 0.207 | 5 | 0.000 | 0.471 | 137.390 |
| interview_40s_closeup | heuristic | interview | 0.560 | 1.000 | 1.000 | 1.000 | 0.075 | 6 | 0.000 | 0.271 | 73.430 |
| interview_40s_closeup | llm | interview | 0.539 | 1.000 | 1.000 | 1.000 | 0.005 | 8 | 0.000 | 0.329 | 196.560 |
| interview_broll_mix | heuristic | interview | 0.561 | 1.000 | 1.000 | 1.000 | 0.018 | 8 | 0.000 | 0.306 | 93.580 |
| interview_broll_mix | llm | interview | 0.609 | 1.000 | 1.000 | 1.000 | 0.244 | 8 | 0.000 | 0.203 | 194.080 |
| narrative_lonely_cook | heuristic | narrative_mixed | 0.515 | 1.000 | 1.000 | — | 0.023 | 17 | 0.150 | 0.355 | 235.290 |
| narrative_lonely_cook | llm | narrative_mixed | 0.510 | 1.000 | 1.000 | — | 0.583 | 7 | 0.300 | 0.259 | 231.420 |
| narrative_documentary_open | heuristic | narrative_mixed | 0.398 | 1.000 | 1.000 | — | 0.389 | 3 | 0.000 | 0.337 | 42.670 |
| narrative_documentary_open | llm | narrative_mixed | 0.551 | 1.000 | 1.000 | — | 0.533 | 3 | 0.000 | 0.312 | 84.350 |
| edge_no_person_hard | heuristic | edge | 0.498 | 1.000 | — | — | 0.093 | 4 | 0.000 | 0.480 | 53.570 |
| edge_no_person_hard | llm | edge | 0.438 | 1.000 | — | — | 0.194 | 5 | 0.000 | 0.383 | 139.670 |
