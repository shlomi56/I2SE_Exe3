# Benchmark results (seed 42)

## Morning up-peak (75 % lobby → floors)

| Algorithm | Execution | Avg Journey (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Overloads | Avg Dispatch Latency (ms) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| FCFS | LOCAL | 23.80 | 8.71 | 37.74 | 219 | 0 | 0.008 |
| SCAN | LOCAL | 24.80 | 4.51 | 20.46 | 187 | 0 | 0.018 |
| MC_DDI | LOCAL | 20.08 | 6.15 | 23.87 | 203 | 0 | 0.039 |

## Midday mixed (50 % inter-floor)

| Algorithm | Execution | Avg Journey (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Overloads | Avg Dispatch Latency (ms) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| FCFS | LOCAL | 19.56 | 6.37 | 20.56 | 118 | 0 | 0.007 |
| SCAN | LOCAL | 18.37 | 3.53 | 8.85 | 111 | 0 | 0.018 |
| MC_DDI | LOCAL | 17.03 | 4.11 | 12.13 | 116 | 0 | 0.031 |

## Evening down-peak (75 % floors → lobby)

| Algorithm | Execution | Avg Journey (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Overloads | Avg Dispatch Latency (ms) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| FCFS | LOCAL | 22.82 | 9.18 | 38.84 | 189 | 0 | 0.006 |
| SCAN | LOCAL | 22.21 | 6.94 | 40.98 | 176 | 0 | 0.016 |
| MC_DDI | LOCAL | 19.34 | 5.83 | 20.27 | 180 | 0 | 0.032 |

## Full day (3 phases, E1 vibration fault 1200–1320 s)

| Algorithm | Execution | Avg Journey (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Overloads | Avg Dispatch Latency (ms) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| FCFS | LOCAL | 21.65 | 8.18 | 37.39 | 388 | 0 | 0.006 |
| SCAN | LOCAL | 21.03 | 5.16 | 24.58 | 355 | 0 | 0.015 |
| MC_DDI | LOCAL | 18.94 | 5.42 | 31.66 | 378 | 0 | 0.031 |
| MC_DDI (ESP32 mock) | ESP32 | 18.94 | 5.42 | 31.66 | 378 | 0 | 0.101 |

## Robustness across seeds (avg journey s / total stops)

| Scenario | Seed | FCFS | SCAN | MC_DDI |
| :--- | ---: | :--- | :--- | :--- |
| up_peak | 42 | 23.80 / 219 | 24.80 / 187 | 20.08 / 203 |
| up_peak | 1 | 26.77 / 208 | 24.78 / 184 | 21.30 / 199 |
| up_peak | 2 | 24.61 / 217 | 24.16 / 182 | 21.11 / 213 |
| up_peak | 3 | 23.07 / 216 | 25.12 / 184 | 20.32 / 207 |
| up_peak | 7 | 23.44 / 220 | 23.02 / 187 | 20.48 / 206 |
| full | 42 | 21.65 / 388 | 21.03 / 355 | 18.94 / 378 |
| full | 1 | 21.71 / 384 | 20.36 / 352 | 18.91 / 371 |
| full | 2 | 21.08 / 386 | 20.85 / 359 | 19.14 / 379 |
| full | 3 | 20.52 / 390 | 20.39 / 349 | 18.06 / 381 |
| full | 7 | 20.59 / 384 | 20.30 / 345 | 18.75 / 373 |
