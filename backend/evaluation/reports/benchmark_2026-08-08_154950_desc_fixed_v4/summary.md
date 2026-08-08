# Benchmark timeline-from-prompt — 2026-08-08_154950
**Tag** : `desc_fixed_v4`

**Prompts exécutés** : 72
**Modes assembler** : baseline_no_filter, baseline_random, baseline_single_shot, heuristic

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic | 18 | 0.180 | 0.528 | 1.000 | 1.000 | 0.427 | 0.723 | 0.552 | 0.325 | 0.000 | 93.232 |
| baseline_random | 18 | 0.000 | 1.000 | — | — | 0.023 | 0.991 | 0.630 | 0.000 | 0.000 | 0.021 |
| baseline_no_filter | 18 | 0.325 | 0.381 | 0.000 | 0.000 | 0.438 | 0.376 | 0.069 | 0.467 | 0.000 | 92.207 |
| baseline_single_shot | 18 | 0.189 | 1.000 | — | — | 0.208 | 0.977 | 0.314 | 0.000 | 0.004 | 35.729 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 5 | 0.218 | 0.900 | — | — | 0.282 | 0.933 | 0.346 | 0.388 | 0.000 | 69.976 |
| edge | 3 | 0.193 | 0.444 | 1.000 | — | 0.281 | 0.727 | 0.637 | 0.284 | 0.000 | 75.753 |
| interview | 5 | 0.091 | 0.200 | 1.000 | 1.000 | 0.471 | 0.400 | 0.384 | 0.256 | 0.000 | 96.794 |
| narrative_mixed | 5 | 0.225 | 0.533 | 1.000 | — | 0.616 | 0.833 | 0.876 | 0.356 | 0.000 | 123.414 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_short | heuristic | broll_nature | 0.158 | 1.000 | — | — | 0.400 | 3 | 0.000 | 0.378 | 57.900 |
| broll_nature_short | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.040 |
| broll_nature_short | baseline_no_filter | broll_nature | 0.081 | 0.667 | — | — | 0.400 | 3 | 0.000 | 0.373 | 39.870 |
| broll_nature_short | baseline_single_shot | broll_nature | 0.256 | 1.000 | — | — | 0.600 | 5 | 0.000 | 0.000 | 36.700 |
| broll_nature_60s | heuristic | broll_nature | 0.142 | 1.000 | — | — | 0.487 | 12 | 0.000 | 0.386 | 139.860 |
| broll_nature_60s | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.000 | 0.020 |
| broll_nature_60s | baseline_no_filter | broll_nature | 0.228 | 0.917 | — | — | 0.600 | 12 | 0.000 | 0.418 | 143.320 |
| broll_nature_60s | baseline_single_shot | broll_nature | 0.229 | 1.000 | — | — | 0.050 | 5 | 0.000 | 0.000 | 35.600 |
| broll_urban | heuristic | broll_nature | 0.266 | 1.000 | — | — | 0.233 | 5 | 0.000 | 0.352 | 63.540 |
| broll_urban | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.033 | 5 | 0.000 | 0.000 | 0.020 |
| broll_urban | baseline_no_filter | broll_nature | 0.297 | 1.000 | — | — | 0.167 | 5 | 0.000 | 0.405 | 59.430 |
| broll_urban | baseline_single_shot | broll_nature | 0.239 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.000 | 33.180 |
| broll_water_calm | heuristic | broll_nature | 0.177 | 1.000 | — | — | 0.050 | 3 | 0.000 | 0.435 | 37.350 |
| broll_water_calm | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.020 |
| broll_water_calm | baseline_no_filter | broll_nature | 0.287 | 1.000 | — | — | 0.050 | 3 | 0.000 | 0.462 | 37.490 |
| broll_water_calm | baseline_single_shot | broll_nature | 0.272 | 1.000 | — | — | 0.250 | 5 | 0.000 | 0.000 | 31.540 |
| broll_energy_dynamic | heuristic | broll_nature | 0.345 | 0.500 | — | — | 0.240 | 4 | 0.000 | 0.387 | 51.230 |
| broll_energy_dynamic | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.040 |
| broll_energy_dynamic | baseline_no_filter | broll_nature | 0.791 | 1.000 | — | — | 0.400 | 5 | 0.000 | 0.560 | 61.660 |
| broll_energy_dynamic | baseline_single_shot | broll_nature | 0.383 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 27.130 |
| interview_40s_closeup | heuristic | interview | 0.107 | 0.500 | 1.000 | 1.000 | 0.300 | 4 | 0.000 | 0.277 | 47.800 |
| interview_40s_closeup | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.020 |
| interview_40s_closeup | baseline_no_filter | interview | 0.475 | 0.000 | 0.000 | 0.000 | 0.000 | 5 | 0.000 | 0.534 | 59.810 |
| interview_40s_closeup | baseline_single_shot | interview | 0.108 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 21.840 |
| interview_60s_serious | heuristic | interview | 0.080 | 0.000 | 1.000 | 1.000 | 0.600 | 12 | 0.000 | 0.298 | 146.460 |
| interview_60s_serious | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.000 | 0.020 |
| interview_60s_serious | baseline_no_filter | interview | 0.383 | 0.000 | 0.000 | 0.000 | 0.600 | 12 | 0.000 | 0.512 | 138.250 |
| interview_60s_serious | baseline_single_shot | interview | 0.023 | 1.000 | — | — | 0.517 | 2 | 0.000 | 0.000 | 15.200 |
| interview_broll_mix | heuristic | interview | 0.106 | 0.500 | 1.000 | 1.000 | 0.556 | 12 | 0.000 | 0.311 | 136.480 |
| interview_broll_mix | baseline_random | interview | 0.000 | 1.000 | — | — | 0.027 | 7 | 0.000 | 0.000 | 0.020 |
| interview_broll_mix | baseline_no_filter | interview | 0.284 | 0.250 | 0.000 | 0.000 | 0.667 | 12 | 0.000 | 0.488 | 138.340 |
| interview_broll_mix | baseline_single_shot | interview | 0.136 | 1.000 | — | — | 0.000 | 12 | 0.077 | 0.000 | 85.680 |
| interview_short | heuristic | interview | 0.083 | 0.000 | 1.000 | 1.000 | 0.000 | 1 | 0.000 | 0.167 | 17.790 |
| interview_short | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.020 |
| interview_short | baseline_no_filter | interview | 0.462 | 0.000 | 0.000 | 0.000 | 0.000 | 1 | 0.000 | 0.524 | 16.710 |
| interview_short | baseline_single_shot | interview | 0.144 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 21.770 |
| interview_multi_speaker | heuristic | interview | 0.080 | 0.000 | 1.000 | 1.000 | 0.900 | 12 | 0.000 | 0.229 | 135.440 |
| interview_multi_speaker | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 7 | 0.000 | 0.000 | 0.010 |
| interview_multi_speaker | baseline_no_filter | interview | 0.170 | 0.000 | 0.000 | 0.000 | 0.920 | 12 | 0.000 | 0.477 | 138.370 |
| interview_multi_speaker | baseline_single_shot | interview | 0.268 | 1.000 | — | — | 0.020 | 5 | 0.000 | 0.000 | 32.850 |
| narrative_lonely_cook | heuristic | narrative_mixed | 0.172 | 0.500 | 1.000 | — | 0.022 | 12 | 0.000 | 0.388 | 142.100 |
| narrative_lonely_cook | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 12 | 0.000 | 0.000 | 0.020 |
| narrative_lonely_cook | baseline_no_filter | narrative_mixed | 0.346 | 0.083 | 0.000 | — | 0.156 | 12 | 0.000 | 0.473 | 138.770 |
| narrative_lonely_cook | baseline_single_shot | narrative_mixed | 0.148 | 1.000 | — | — | 0.478 | 6 | 0.000 | 0.000 | 43.930 |
| narrative_documentary_open | heuristic | narrative_mixed | 0.227 | 0.667 | 1.000 | — | 0.333 | 3 | 0.000 | 0.372 | 50.820 |
| narrative_documentary_open | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.033 | 5 | 0.000 | 0.000 | 0.020 |
| narrative_documentary_open | baseline_no_filter | narrative_mixed | 0.282 | 0.333 | 0.000 | — | 0.267 | 3 | 0.000 | 0.475 | 44.970 |
| narrative_documentary_open | baseline_single_shot | narrative_mixed | 0.194 | 1.000 | — | — | 0.333 | 3 | 0.000 | 0.000 | 22.830 |
| narrative_music_video | heuristic | narrative_mixed | 0.195 | 0.250 | — | — | 0.933 | 12 | 0.000 | 0.315 | 138.380 |
| narrative_music_video | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.027 | 7 | 0.000 | 0.000 | 0.020 |
| narrative_music_video | baseline_no_filter | narrative_mixed | 0.282 | 0.083 | — | — | 0.622 | 12 | 0.000 | 0.445 | 143.120 |
| narrative_music_video | baseline_single_shot | narrative_mixed | 0.143 | 1.000 | — | — | 0.022 | 9 | 0.000 | 0.000 | 57.290 |
| narrative_teaser_energetic | heuristic | narrative_mixed | 0.372 | 0.667 | — | — | 1.450 | 12 | 0.000 | 0.332 | 142.300 |
| narrative_teaser_energetic | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.010 |
| narrative_teaser_energetic | baseline_no_filter | narrative_mixed | 0.374 | 0.083 | — | — | 1.450 | 12 | 0.000 | 0.463 | 136.540 |
| narrative_teaser_energetic | baseline_single_shot | narrative_mixed | 0.145 | 1.000 | — | — | 0.550 | 9 | 0.000 | 0.000 | 51.310 |
| narrative_slow_atmospheric | heuristic | narrative_mixed | 0.157 | 0.583 | 1.000 | — | 0.342 | 12 | 0.000 | 0.375 | 143.470 |
| narrative_slow_atmospheric | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.232 | 12 | 0.000 | 0.000 | 0.020 |
| narrative_slow_atmospheric | baseline_no_filter | narrative_mixed | 0.294 | 0.500 | — | — | 0.358 | 12 | 0.000 | 0.449 | 140.180 |
| narrative_slow_atmospheric | baseline_single_shot | narrative_mixed | 0.121 | 1.000 | — | — | 0.067 | 9 | 0.000 | 0.000 | 59.720 |
| edge_very_short | heuristic | edge | 0.365 | 0.333 | — | — | 0.000 | 3 | 0.000 | 0.405 | 39.920 |
| edge_very_short | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 2 | 0.000 | 0.000 | 0.010 |
| edge_very_short | baseline_no_filter | edge | 0.394 | 0.333 | — | — | 0.000 | 3 | 0.000 | 0.466 | 36.430 |
| edge_very_short | baseline_single_shot | edge | 0.076 | 1.000 | — | — | 0.188 | 3 | 0.000 | 0.000 | 21.180 |
| edge_no_person_hard | heuristic | edge | 0.116 | 1.000 | — | — | 0.133 | 5 | 0.000 | 0.248 | 63.750 |
| edge_no_person_hard | baseline_random | edge | 0.000 | 1.000 | — | — | 0.033 | 5 | 0.000 | 0.000 | 0.030 |
| edge_no_person_hard | baseline_no_filter | edge | 0.225 | 0.600 | — | — | 0.267 | 5 | 0.000 | 0.413 | 61.610 |
| edge_no_person_hard | baseline_single_shot | edge | 0.250 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 24.930 |
| edge_all_closeup | heuristic | edge | 0.096 | 0.000 | 1.000 | — | 0.711 | 11 | 0.000 | 0.200 | 123.590 |
| edge_all_closeup | baseline_random | edge | 0.000 | 1.000 | — | — | 0.027 | 7 | 0.000 | 0.000 | 0.020 |
| edge_all_closeup | baseline_no_filter | edge | 0.200 | 0.000 | 0.000 | — | 0.956 | 11 | 0.000 | 0.462 | 124.860 |
| edge_all_closeup | baseline_single_shot | edge | 0.260 | 1.000 | — | — | 0.667 | 3 | 0.000 | 0.000 | 20.440 |
