# Benchmark timeline-from-prompt — 2026-08-08_135506
**Tag** : `full_fixes_v3`

**Prompts exécutés** : 72
**Modes assembler** : baseline_no_filter, baseline_random, baseline_single_shot, heuristic

## Statistiques globales

| mode | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic | 18 | 0.142 | 0.475 | 1.000 | 1.000 | 0.349 | 0.708 | 0.445 | 0.316 | 0.000 | 91.463 |
| baseline_random | 18 | 0.000 | 1.000 | — | — | 0.015 | 0.991 | 0.868 | 0.000 | 0.000 | 0.023 |
| baseline_no_filter | 18 | 0.218 | 0.349 | 0.076 | 0.083 | 0.498 | 0.416 | 0.452 | 0.460 | 0.000 | 91.033 |
| baseline_single_shot | 18 | 0.163 | 1.000 | — | — | 0.255 | 0.950 | 0.451 | 0.000 | 0.000 | 35.817 |

## Moyennes par profil (mode heuristic uniquement si présent, sinon 1er mode)

| profile | n | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | clip_diversity | framing_entropy | avg_top1_score | skipped_ratio | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature | 5 | 0.158 | 0.800 | — | — | 0.247 | 0.927 | 0.144 | 0.317 | 0.000 | 70.900 |
| edge | 3 | 0.071 | 0.556 | 1.000 | — | 0.150 | 0.727 | 0.331 | 0.319 | 0.000 | 75.710 |
| interview | 5 | 0.134 | 0.080 | 1.000 | 1.000 | 0.499 | 0.431 | 0.363 | 0.344 | 0.000 | 101.060 |
| narrative_mixed | 5 | 0.175 | 0.498 | 1.000 | — | 0.420 | 0.755 | 0.897 | 0.284 | 0.000 | 111.882 |

## Résultats par prompt

| prompt_id | mode | profile | coverage_mean | framing_precision | speaker_precision | dialogue_precision | duration_deviation_pct | n_segments | skipped_ratio | avg_top1_score | wall_total_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broll_nature_short | heuristic | broll_nature | 0.208 | 1.000 | — | — | 0.400 | 3 | 0.000 | 0.380 | 64.600 |
| broll_nature_short | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 2 | 0.000 | 0.000 | 0.100 |
| broll_nature_short | baseline_no_filter | broll_nature | 0.165 | 0.667 | — | — | 0.200 | 3 | 0.000 | 0.401 | 39.330 |
| broll_nature_short | baseline_single_shot | broll_nature | 0.170 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 34.110 |
| broll_nature_60s | heuristic | broll_nature | 0.125 | 1.000 | — | — | 0.650 | 11 | 0.000 | 0.385 | 127.790 |
| broll_nature_60s | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.000 | 0.020 |
| broll_nature_60s | baseline_no_filter | broll_nature | 0.097 | 1.000 | — | — | 0.377 | 11 | 0.000 | 0.408 | 131.040 |
| broll_nature_60s | baseline_single_shot | broll_nature | 0.274 | 1.000 | — | — | 0.000 | 7 | 0.000 | 0.000 | 48.430 |
| broll_urban | heuristic | broll_nature | 0.212 | 1.000 | — | — | 0.133 | 5 | 0.000 | 0.212 | 65.420 |
| broll_urban | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.020 |
| broll_urban | baseline_no_filter | broll_nature | 0.445 | 1.000 | — | — | 1.567 | 11 | 0.000 | 0.495 | 133.420 |
| broll_urban | baseline_single_shot | broll_nature | 0.188 | 1.000 | — | — | 0.333 | 7 | 0.000 | 0.000 | 43.270 |
| broll_water_calm | heuristic | broll_nature | 0.098 | 1.000 | — | — | 0.050 | 3 | 0.000 | 0.223 | 38.070 |
| broll_water_calm | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.010 |
| broll_water_calm | baseline_no_filter | broll_nature | 0.350 | 0.500 | — | — | 0.250 | 4 | 0.000 | 0.454 | 50.300 |
| broll_water_calm | baseline_single_shot | broll_nature | 0.166 | 1.000 | — | — | 0.000 | 5 | 0.000 | 0.000 | 29.940 |
| broll_energy_dynamic | heuristic | broll_nature | 0.148 | 0.000 | — | — | 0.000 | 5 | 0.000 | 0.384 | 58.620 |
| broll_energy_dynamic | baseline_random | broll_nature | 0.000 | 1.000 | — | — | 0.008 | 4 | 0.000 | 0.000 | 0.020 |
| broll_energy_dynamic | baseline_no_filter | broll_nature | 0.370 | 0.000 | — | — | 0.200 | 5 | 0.000 | 0.455 | 60.200 |
| broll_energy_dynamic | baseline_single_shot | broll_nature | 0.139 | 1.000 | — | — | 0.060 | 5 | 0.000 | 0.000 | 31.690 |
| interview_40s_closeup | heuristic | interview | 0.099 | 0.000 | 1.000 | 1.000 | 0.925 | 11 | 0.000 | 0.420 | 125.340 |
| interview_40s_closeup | baseline_random | interview | 0.000 | 1.000 | — | — | 0.030 | 6 | 0.000 | 0.000 | 0.010 |
| interview_40s_closeup | baseline_no_filter | interview | 0.362 | 0.364 | 0.000 | 0.000 | 0.925 | 11 | 0.000 | 0.500 | 119.080 |
| interview_40s_closeup | baseline_single_shot | interview | 0.119 | 1.000 | — | — | 0.475 | 4 | 0.000 | 0.000 | 25.420 |
| interview_60s_serious | heuristic | interview | 0.182 | 0.000 | 1.000 | 1.000 | 0.467 | 11 | 0.000 | 0.500 | 128.690 |
| interview_60s_serious | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 8 | 0.000 | 0.000 | 0.020 |
| interview_60s_serious | baseline_no_filter | interview | 0.291 | 0.000 | 0.273 | 0.500 | 0.787 | 11 | 0.000 | 0.507 | 132.010 |
| interview_60s_serious | baseline_single_shot | interview | 0.171 | 1.000 | — | — | 0.733 | 3 | 0.000 | 0.000 | 20.140 |
| interview_broll_mix | heuristic | interview | 0.121 | 0.400 | 1.000 | 1.000 | 0.444 | 10 | 0.000 | 0.293 | 112.670 |
| interview_broll_mix | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.020 |
| interview_broll_mix | baseline_no_filter | interview | 0.227 | 0.200 | 0.000 | 0.000 | 0.778 | 10 | 0.000 | 0.473 | 112.060 |
| interview_broll_mix | baseline_single_shot | interview | 0.293 | 1.000 | — | — | 0.556 | 6 | 0.000 | 0.000 | 40.180 |
| interview_short | heuristic | interview | 0.153 | 0.000 | 1.000 | 1.000 | 0.000 | 1 | 0.000 | 0.174 | 17.860 |
| interview_short | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 2 | 0.000 | 0.000 | 0.020 |
| interview_short | baseline_no_filter | interview | 0.082 | 0.000 | 0.000 | 0.000 | 0.519 | 1 | 0.000 | 0.488 | 16.420 |
| interview_short | baseline_single_shot | interview | 0.138 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 21.970 |
| interview_multi_speaker | heuristic | interview | 0.116 | 0.000 | 1.000 | 1.000 | 0.660 | 11 | 0.000 | 0.332 | 120.740 |
| interview_multi_speaker | baseline_random | interview | 0.000 | 1.000 | — | — | 0.000 | 7 | 0.000 | 0.000 | 0.020 |
| interview_multi_speaker | baseline_no_filter | interview | 0.011 | 0.000 | 0.000 | 0.000 | 0.565 | 11 | 0.000 | 0.484 | 114.470 |
| interview_multi_speaker | baseline_single_shot | interview | 0.085 | 1.000 | — | — | 0.320 | 5 | 0.000 | 0.000 | 28.920 |
| narrative_lonely_cook | heuristic | narrative_mixed | 0.176 | 0.455 | 1.000 | — | 0.156 | 11 | 0.000 | 0.332 | 127.160 |
| narrative_lonely_cook | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 12 | 0.000 | 0.000 | 0.020 |
| narrative_lonely_cook | baseline_no_filter | narrative_mixed | 0.298 | 0.455 | 0.400 | — | 0.209 | 11 | 0.000 | 0.461 | 141.690 |
| narrative_lonely_cook | baseline_single_shot | narrative_mixed | 0.254 | 1.000 | — | — | 0.233 | 9 | 0.000 | 0.000 | 55.390 |
| narrative_documentary_open | heuristic | narrative_mixed | 0.228 | 0.500 | 1.000 | — | 0.333 | 6 | 0.000 | 0.293 | 76.230 |
| narrative_documentary_open | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.020 |
| narrative_documentary_open | baseline_no_filter | narrative_mixed | 0.231 | 0.333 | 0.000 | — | 0.360 | 3 | 0.000 | 0.471 | 42.860 |
| narrative_documentary_open | baseline_single_shot | narrative_mixed | 0.059 | 1.000 | — | — | 0.333 | 3 | 0.000 | 0.000 | 22.430 |
| narrative_music_video | heuristic | narrative_mixed | 0.130 | 0.545 | — | — | 0.444 | 11 | 0.000 | 0.249 | 126.940 |
| narrative_music_video | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.020 |
| narrative_music_video | baseline_no_filter | narrative_mixed | 0.144 | 0.091 | — | — | 0.489 | 11 | 0.000 | 0.449 | 130.490 |
| narrative_music_video | baseline_single_shot | narrative_mixed | 0.096 | 1.000 | — | — | 0.456 | 13 | 0.000 | 0.000 | 82.770 |
| narrative_teaser_energetic | heuristic | narrative_mixed | 0.146 | 0.444 | 1.000 | — | 0.750 | 9 | 0.000 | 0.282 | 100.610 |
| narrative_teaser_energetic | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 0.020 |
| narrative_teaser_energetic | baseline_no_filter | narrative_mixed | 0.089 | 0.000 | 0.000 | 0.000 | 0.050 | 5 | 0.000 | 0.456 | 58.490 |
| narrative_teaser_energetic | baseline_single_shot | narrative_mixed | 0.161 | 1.000 | — | — | 0.200 | 7 | 0.000 | 0.000 | 40.970 |
| narrative_slow_atmospheric | heuristic | narrative_mixed | 0.195 | 0.545 | — | — | 0.417 | 11 | 0.000 | 0.266 | 128.470 |
| narrative_slow_atmospheric | baseline_random | narrative_mixed | 0.000 | 1.000 | — | — | 0.232 | 12 | 0.000 | 0.000 | 0.020 |
| narrative_slow_atmospheric | baseline_no_filter | narrative_mixed | 0.240 | 0.545 | — | — | 0.405 | 11 | 0.000 | 0.425 | 128.060 |
| narrative_slow_atmospheric | baseline_single_shot | narrative_mixed | 0.123 | 1.000 | — | — | 0.040 | 8 | 0.000 | 0.000 | 54.840 |
| edge_very_short | heuristic | edge | 0.081 | 0.667 | — | — | 0.062 | 3 | 0.000 | 0.352 | 40.830 |
| edge_very_short | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 1 | 0.000 | 0.000 | 0.020 |
| edge_very_short | baseline_no_filter | edge | 0.062 | 0.333 | 0.000 | — | 0.188 | 3 | 0.000 | 0.460 | 38.590 |
| edge_very_short | baseline_single_shot | edge | 0.144 | 1.000 | — | — | 0.000 | 3 | 0.000 | 0.000 | 19.580 |
| edge_no_person_hard | heuristic | edge | 0.034 | 1.000 | — | — | 0.167 | 5 | 0.000 | 0.376 | 61.010 |
| edge_no_person_hard | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 4 | 0.000 | 0.000 | 0.020 |
| edge_no_person_hard | baseline_no_filter | edge | 0.168 | 0.800 | — | — | 0.133 | 5 | 0.000 | 0.401 | 61.480 |
| edge_no_person_hard | baseline_single_shot | edge | 0.069 | 1.000 | — | — | 0.100 | 5 | 0.000 | 0.000 | 29.160 |
| edge_all_closeup | heuristic | edge | 0.097 | 0.000 | 1.000 | — | 0.222 | 11 | 0.000 | 0.228 | 125.290 |
| edge_all_closeup | baseline_random | edge | 0.000 | 1.000 | — | — | 0.000 | 6 | 0.000 | 0.000 | 0.020 |
| edge_all_closeup | baseline_no_filter | edge | 0.291 | 0.000 | 0.091 | — | 0.956 | 11 | 0.000 | 0.488 | 128.600 |
| edge_all_closeup | baseline_single_shot | edge | 0.283 | 1.000 | — | — | 0.756 | 2 | 0.000 | 0.000 | 15.500 |
