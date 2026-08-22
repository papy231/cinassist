# Benchmark timeline-from-prompt — 2026-08-21_182031
**Tag** : `pinky_only_v5`

**Prompts exécutés** : 69
**Modes assembler** : baseline_no_filter, baseline_random, baseline_single_shot, heuristic

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic | 17 | 0.228 | 0.717 | 1.000 | 1.000 | 0.007 | 0.944 | 0.654 | 0.352 | 0.000 | 95.366 |
| baseline_random | 18 | 0.000 | 1.000 | — | — | 0.002 | 1.000 | 0.424 | 0.000 | 0.000 | 0.044 |
| baseline_no_filter | 16 | 0.223 | 0.152 | 0.655 | 0.613 | 0.275 | 0.609 | 0.781 | 0.442 | 0.000 | 85.676 |
| baseline_single_shot | 18 | 0.194 | 1.000 | — | — | 0.127 | 0.960 | 0.922 | 0.000 | 0.000 | 52.483 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 5 | 0.193 | 1.000 | — | — | 0.013 | 1.000 | 0.000 | 0.324 | 0.000 | 65.798 |
| edge | 3 | 0.238 | 0.556 | 1.000 | 1.000 | 0.000 | 1.000 | 0.855 | 0.370 | 0.000 | 84.087 |
| interview | 5 | 0.226 | 0.400 | 1.000 | 1.000 | 0.000 | 1.000 | 0.611 | 0.377 | 0.000 | 106.510 |
| narrative_mixed | 4 | 0.265 | 0.881 | 1.000 | 1.000 | 0.013 | 0.762 | 1.374 | 0.345 | 0.000 | 126.855 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_short | heuristic | broll_nature | 0.139 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.272 | 50.150 |
| broll_nature_short | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 2 | 0.000 | 0.000 | 0.040 |
| broll_nature_short | baseline_no_filter | broll_nature | 0.197 | 0.000 | — | — | 0.400 | 3 | 0.000 | 0.428 | 42.480 |
| broll_nature_short | baseline_single_shot | broll_nature | 0.365 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.000 | 88.700 |
| broll_nature_60s | heuristic | broll_nature | 0.144 | 1.000 | — | — | 0.067 | 7 | 0.000 | 0.392 | 91.600 |
| broll_nature_60s | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.000 | 0.040 |
| broll_nature_60s | baseline_no_filter | broll_nature | 0.128 | 0.000 | — | — | 0.067 | 8 | 0.000 | 0.411 | 102.420 |
| broll_nature_60s | baseline_single_shot | broll_nature | 0.274 | 1.000 | — | — | 0.083 | 9 | 0.000 | 0.000 | 76.400 |
| broll_urban | heuristic | broll_nature | 0.199 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.236 | 69.090 |
| broll_urban | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.070 |
| broll_urban | baseline_no_filter | broll_nature | 0.157 | 0.000 | — | — | 0.167 | 5 | 0.000 | 0.413 | 67.300 |
| broll_urban | baseline_single_shot | broll_nature | 0.336 | 1.000 | — | — | 0.100 | 6 | 0.000 | 0.000 | 43.120 |
| broll_water_calm | heuristic | broll_nature | 0.152 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.254 | 53.600 |
| broll_water_calm | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.040 |
| broll_water_calm | baseline_no_filter | broll_nature | 0.086 | 0.000 | — | — | 0.050 | 3 | 0.000 | 0.398 | 39.530 |
| broll_water_calm | baseline_single_shot | broll_nature | 0.317 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 35.670 |
| broll_energy_dynamic | heuristic | broll_nature | 0.332 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.463 | 64.550 |
| broll_energy_dynamic | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.040 | 4 | 0.000 | 0.000 | 0.030 |
| broll_energy_dynamic | baseline_no_filter | broll_nature | 0.228 | 0.400 | 1.000 | 1.000 | 0.000 | 5 | 0.000 | 0.437 | 62.180 |
| broll_energy_dynamic | baseline_single_shot | broll_nature | 0.273 | 1.000 | — | — | 0.340 | 8 | 0.000 | 0.000 | 65.640 |
| interview_40s_closeup | heuristic | interview | 0.276 | 0.500 | 1.000 | 1.000 | 0.000 | 6 | 0.000 | 0.418 | 73.710 |
| interview_40s_closeup | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.000 | 0.040 |
| interview_40s_closeup | baseline_no_filter | interview | 0.285 | 0.500 | 0.625 | 0.625 | 0.400 | 8 | 0.000 | 0.459 | 97.020 |
| interview_40s_closeup | baseline_single_shot | interview | 0.096 | 1.000 | — | — | 0.250 | 5 | 0.000 | 0.000 | 36.260 |
| interview_60s_serious | heuristic | interview | 0.236 | 0.000 | 1.000 | 1.000 | 0.000 | 19 | 0.000 | 0.316 | 236.420 |
| interview_60s_serious | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.000 | 0.040 |
| interview_60s_serious | baseline_no_filter | interview | 0.189 | 0.000 | 0.400 | 0.667 | 0.333 | 10 | 0.000 | 0.461 | 120.410 |
| interview_60s_serious | baseline_single_shot | interview | 0.226 | 1.000 | — | — | 0.203 | 5 | 0.000 | 0.000 | 45.720 |
| interview_broll_mix | heuristic | interview | 0.239 | 0.500 | 1.000 | 1.000 | 0.000 | 10 | 0.000 | 0.330 | 119.850 |
| interview_broll_mix | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.050 |
| interview_broll_mix | baseline_no_filter | interview | 0.241 | 0.000 | 1.000 | 1.000 | 1.311 | 16 | 0.000 | 0.453 | 191.430 |
| interview_broll_mix | baseline_single_shot | interview | 0.142 | 1.000 | — | — | 0.204 | 6 | 0.000 | 0.000 | 50.950 |
| interview_short | heuristic | interview | 0.169 | 0.000 | 1.000 | 1.000 | 0.000 | 1 | 0.000 | 0.450 | 17.890 |
| interview_short | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 2 | 0.000 | 0.000 | 0.030 |
| interview_short | baseline_no_filter | interview | 0.172 | 0.000 | 0.000 | 0.000 | 0.025 | 1 | 0.000 | 0.450 | 16.620 |
| interview_short | baseline_single_shot | interview | 0.074 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 24.550 |
| interview_multi_speaker | heuristic | interview | 0.211 | 1.000 | 1.000 | 1.000 | 0.000 | 7 | 0.000 | 0.369 | 84.680 |
| interview_multi_speaker | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 7 | 0.000 | 0.000 | 0.050 |
| interview_multi_speaker | baseline_single_shot | interview | 0.071 | 1.000 | — | — | 0.220 | 8 | 0.000 | 0.000 | 63.990 |
| narrative_lonely_cook | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 12 | 0.000 | 0.000 | 0.090 |
| narrative_lonely_cook | baseline_single_shot | narrative_mixed | 0.183 | 1.000 | — | — | 0.417 | 7 | 0.000 | 0.000 | 62.630 |
| narrative_documentary_open | heuristic | narrative_mixed | 0.300 | 0.667 | 1.000 | 1.000 | 0.000 | 6 | 0.000 | 0.412 | 79.590 |
| narrative_documentary_open | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.060 |
| narrative_documentary_open | baseline_no_filter | narrative_mixed | 0.276 | 0.500 | 1.000 | — | 0.400 | 6 | 0.000 | 0.466 | 75.290 |
| narrative_documentary_open | baseline_single_shot | narrative_mixed | 0.056 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 28.750 |
| narrative_music_video | heuristic | narrative_mixed | 0.243 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.333 | 98.720 |
| narrative_music_video | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.030 |
| narrative_music_video | baseline_no_filter | narrative_mixed | 0.175 | 0.000 | — | — | 0.333 | 10 | 0.000 | 0.419 | 117.790 |
| narrative_music_video | baseline_single_shot | narrative_mixed | 0.165 | 1.000 | — | — | 0.000 | 9 | 0.000 | 0.000 | 67.410 |
| narrative_teaser_energetic | heuristic | narrative_mixed | 0.217 | 0.857 | 1.000 | — | 0.050 | 7 | 0.000 | 0.351 | 86.980 |
| narrative_teaser_energetic | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.030 |
| narrative_teaser_energetic | baseline_no_filter | narrative_mixed | 0.311 | 0.500 | 0.500 | 0.000 | 0.050 | 6 | 0.000 | 0.476 | 73.040 |
| narrative_teaser_energetic | baseline_single_shot | narrative_mixed | 0.131 | 1.000 | — | — | 0.000 | 7 | 0.000 | 0.000 | 57.980 |
| narrative_slow_atmospheric | heuristic | narrative_mixed | 0.300 | 1.000 | — | — | 0.000 | 19 | 0.000 | 0.282 | 242.130 |
| narrative_slow_atmospheric | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 16 | 0.000 | 0.000 | 0.040 |
| narrative_slow_atmospheric | baseline_no_filter | narrative_mixed | 0.319 | 0.000 | — | — | 0.358 | 12 | 0.000 | 0.465 | 152.650 |
| narrative_slow_atmospheric | baseline_single_shot | narrative_mixed | 0.259 | 1.000 | — | — | 0.049 | 9 | 0.000 | 0.000 | 76.210 |
| edge_very_short | heuristic | edge | 0.322 | 0.667 | 1.000 | 1.000 | 0.000 | 3 | 0.000 | 0.408 | 39.240 |
| edge_very_short | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 1 | 0.000 | 0.000 | 0.040 |
| edge_very_short | baseline_no_filter | edge | 0.220 | 0.333 | — | — | 0.000 | 3 | 0.000 | 0.484 | 42.130 |
| edge_very_short | baseline_single_shot | edge | 0.275 | 1.000 | — | — | 0.250 | 3 | 0.000 | 0.000 | 29.770 |
| edge_no_person_hard | heuristic | edge | 0.172 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.291 | 66.850 |
| edge_no_person_hard | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.040 |
| edge_no_person_hard | baseline_no_filter | edge | 0.190 | 0.200 | — | — | 0.167 | 5 | 0.000 | 0.396 | 67.830 |
| edge_no_person_hard | baseline_single_shot | edge | 0.176 | 1.000 | — | — | 0.167 | 3 | 0.000 | 0.000 | 22.600 |
| edge_all_closeup | heuristic | edge | 0.220 | 0.000 | 1.000 | 1.000 | 0.000 | 12 | 0.000 | 0.409 | 146.170 |
| edge_all_closeup | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.040 |
| edge_all_closeup | baseline_no_filter | edge | 0.391 | 0.000 | 0.714 | 1.000 | 0.335 | 9 | 0.000 | 0.458 | 102.700 |
| edge_all_closeup | baseline_single_shot | edge | 0.077 | 1.000 | — | — | 0.000 | 9 | 0.000 | 0.000 | 68.350 |
