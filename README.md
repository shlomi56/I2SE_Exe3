# Smart Elevator Allocation System (Ex3)
**Course:** Introduction to Software Engineering (OOP / OOD)
**Scope:** End-to-end online Destination Dispatch: optimization, simulation, GUI and ESP32 hardware-in-the-loop (HIL)

---

## 1. Project Overview
A multi-story building has a fleet of smart elevators. Passengers enter their destination at a hall
kiosk (`time, src, target`), and the controller must assign a car **immediately and irrevocably**, so as
to minimize **total journey time** (hall wait + in-cabin time). It must also respect kinematics,
headcount and weight limits, and the sensor safety interlocks. This is an online multi-vehicle
Dial-a-Ride problem (see [docs/formal_optimization_problem_markdown.md](docs/formal_optimization_problem_markdown.md)).

### Core features
* **MC-DDI (Marginal-Cost Destination Dispatch Insertion):** for each call, MC-DDI tries every
  order-preserving pickup and drop-off insertion on every healthy car. Stops at the same floor are merged,
  the projected weight is checked against the safety buffer, and the call goes to the insertion that
  adds the least total journey time. It is benchmarked against **FCFS** and **SCAN**.
* **Physics and sensors:**
  * Exact leg timing: `t_start + |Δfloor|/v + t_stop`.
  * Door cycles, and a no-stop point for moving cars (they can't stop at a floor they are already committed past).
  * Live load sensor, vibration faults and stochastic door obstructions.
  * A reactive **overload interlock**: an overloaded car holds its doors, the bumped passenger is re-dispatched (`REALLOC`) keeping the original call time, and the car is locked against pickups until someone gets off.
* **Decoupled architecture:** the engine notifies observers (`ISimulationObserver`) of every
  event. Dispatchers plug in through `IDispatcher`. The headless core has **zero GUI imports**, which a
  test checks.
* **ESP32 HIL:** the same MC-DDI runs as MicroPython firmware on an ESP32, reached over Wi-Fi TCP
  through `ESP32DispatcherProxy`. It matches the PC decisions bit for bit, and falls back safely if the link drops.
* **Streamlit dashboard:** live building shafts, alerts, KPIs, and a side-by-side comparison of all
  three algorithms.

---

## 2. Repository Structure
```text
smart-elevator-system-EX3/
├── README.md
├── requirements.txt                 # pytest + streamlit (core, generator and TCP bridge use the stdlib only)
├── pyproject.toml                   # Python >= 3.10, pytest configuration
├── simulation_config.json           # building, fleet, sensors, dispatcher, traffic and esp32_bridge settings
├── .streamlit/config.toml           # dashboard light theme
├── docs/                            # engineering specifications (source of truth)
│   ├── software_requirements_specification_srs.md
│   ├── statement_of_work_sow.md
│   ├── formal_optimization_problem_markdown.md       # ToDo #2
│   ├── high_level_design_markdown.md                 # ToDo #3
│   ├── low_level_design_markdown.md                  # ToDo #4
│   ├── allocation_method_design_markdown.md          # ToDo #5
│   ├── simulation_configuration_documentation.md     # ToDo #6
│   ├── request_format_auto_generator_spec_markdown.md # ToDo #7
│   ├── testing_strategy_markdown.md                  # ToDo #9
│   └── simulator_output_format_spec_markdown.md      # ToDo #10
├── src/
│   ├── models/          # enums, DTOs (BuildingConfig, Request, SensorSnapshot, ...) and route_semantics (shared rules)
│   ├── simulation/      # SensorSuite, Elevator, Building, SimulationEngine, ISimulationObserver, SimulationLogger
│   ├── dispatchers/     # IDispatcher, RouteEvaluator, FCFSDispatcher, SCANDispatcher,
│   │                    # MarginalCostDDDispatcher, ESP32DispatcherProxy
│   ├── comms/           # ITransport, WiFiTCPTransport, BLEGattTransport (stub), JSON protocol codec
│   ├── gui/             # Streamlit dashboard: replay recorder, HTML renderer, Altair charts, app.py
│   ├── config_loader.py       # fail-fast JSON validation
│   ├── request_generator.py   # ToDo #7 module and CLI
│   ├── benchmark.py           # multi-scenario benchmark CLI
│   └── main.py                # simulator entry point (headless / --gui / --esp32)
├── esp32/               # MicroPython firmware: EdgeAllocator, FleetStateMirror, ProtocolCodec,
│                        # WirelessServer, EdgeNode, main.py, plus README.md (flashing guide)
├── data/                # requests.csv (full day) + requests_{up_peak,midday,down_peak}.csv
├── outputs/
│   └── benchmarks/      # benchmark_results.{md,json} + <scenario>/<ALGO>/simulation_{output.csv,summary.json}
└── tests/               # pytest suite: unit, integration, benchmark, GUI, ESP32 bridge (+ opt-in HIL)
```

---

## 3. Assignment Deliverables Map
| ToDo | Deliverable | Status | Location |
| :--- | :--- | :--- | :--- |
| #1 | Repository | Done | this README and the layout above |
| #2 | Optimization problem | Done | `docs/formal_optimization_problem_markdown.md` |
| #3 | High-level design (PC + ESP32) | Done | `docs/high_level_design_markdown.md` |
| #4 | Low-level design | Done (implemented) | `docs/low_level_design_markdown.md` → `src/` |
| #5 | Allocation method (MC-DDI) | Done (implemented) | `docs/allocation_method_design_markdown.md` → `src/dispatchers/` |
| #6 | Simulation parameters JSON | Done | `simulation_config.json`, `src/config_loader.py` |
| #7 | Request list and auto-generator | Done | `src/request_generator.py` → `data/requests.csv` |
| #8 | Building simulator and GUI | Done | `src/simulation/`, `src/gui/` |
| #9 | Testing strategy and suite | Done | `docs/testing_strategy_markdown.md` → `tests/` |
| #10 | Output format and stop log | Done | `src/simulation/logger.py` → `simulation_output.csv`, `simulation_summary.json` |
| #11 | ESP32 allocation and Wi-Fi link | Done (verified against a host-side mock; on-board run pending hardware) | `esp32/`, `src/comms/`, `src/dispatchers/esp32_proxy.py` |
| #12 | Experimental results | Done | Section 6 and `outputs/benchmarks/` |

---

## 4. Setup and Usage
All commands run from the repository root.

### 4.1 Install (Python 3.10+)
```bash
uv venv .venv --python 3.12                              # or: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt   # or: .venv/bin/pip install -r requirements.txt
source .venv/bin/activate                                # Windows: .venv\Scripts\activate
```

### 4.2 Generate requests (ToDo #7)
```bash
python -m src.request_generator --config simulation_config.json --output data/requests.csv --seed 42
python -m src.request_generator --config simulation_config.json --output data/requests_3col.csv --strict-3col
```
The output is reproducible byte for byte. `--strict-3col` drops the hidden `weight_kg` column. The
simulator accepts either format; when weights are missing it uses `passengers.avg_weight_kg`.

### 4.3 Run the simulator headless (ToDo #8, #10)
```bash
python -m src.main --config simulation_config.json --requests data/requests.csv --algorithm MC_DDI
python -m src.main --config simulation_config.json --requests data/requests.csv --algorithm SCAN --output-dir outputs/scan
```
Each run writes `simulation_output.csv` (`time,src,target,elevator_id,event_type` with `ALLOC` /
`REALLOC` / `STOP` rows) and `simulation_summary.json` (KPIs) to `--output-dir` (default `outputs/`).
Without `--algorithm`, the run uses `dispatcher.algorithm`, or the ESP32 when `esp32_bridge.enabled` is true.

### 4.4 Streamlit dashboard
```bash
streamlit run src/gui/app.py                 # http://localhost:8501
python -m src.main --config simulation_config.json --gui     # same thing
```
| View | What it shows |
| :--- | :--- |
| **Single** | One algorithm: every floor (B2–15, lobby marked) and one shaft per car. Each car shows its position, direction arrow and passenger count, coloured by state (Moving / Doors open / Overloaded / Fault / Idle, always with a text label). Car cards show passenger and weight bars, next stops, and VIBRATION / DOOR OBSTRUCTED / AT CAPACITY flags. Waiting queues are shown per floor with destination and assigned car. Also: running KPI cards, active alerts and a recent-activity feed. |
| **Compare all** | FCFS, SCAN and MC-DDI side by side, all served the *same* request stream and synced to the same timeline, each with compact KPIs. |
| **Algorithm comparison** | Below the live view: one bar chart per KPI (avg journey, avg and max hall wait, total stops), a running average-journey-time line chart, and a table view. |

Sidebar controls: traffic profile (full day / up-peak / midday / down-peak), seed, view, algorithm,
Play/Pause/Restart, speed (1–120×), and **Jump to** notable moments (fault window, overload bumps,
door obstructions). The timeline scrubber covers the whole run. Each run is simulated headlessly once,
recorded through `ISimulationObserver` and then replayed, so the GUI never drives the core.

### 4.5 Full benchmark (ToDo #12)
```bash
python -m src.benchmark --esp32-mock
```
This command:
* writes `data/requests*.csv`, plus one `simulation_output.csv` and `simulation_summary.json` per scenario and algorithm under `outputs/benchmarks/`;
* writes `outputs/benchmarks/benchmark_results.md` and `.json`, including a multi-seed robustness table;
* with `--esp32-mock`, also runs MC-DDI through the real `esp32/` firmware over local TCP.

### 4.6 Test suite (ToDo #9)
```bash
pytest tests/ -v              # everything except real hardware (~14 s)
pytest -m benchmark           # Layer 3 benchmark regression only
```

### 4.7 ESP32 hardware-in-the-loop (ToDo #11)
1. Flash MicroPython and upload `esp32/*.py`, with `config.py` holding your Wi-Fi credentials. The step-by-step guide is in [esp32/README.md](esp32/README.md).
2. Read the board's IP address from the serial console (115200 baud).
3. Run the simulator through the board:
   ```bash
   python -m src.main --config simulation_config.json --requests data/requests.csv --esp32 192.168.1.105:5000
   ```
   Alternatively, set `"esp32_bridge": {"enabled": true, "host": "<ip>", "port": 5000}` in the config.
4. Run the equivalence and latency test on the board:
   ```bash
   ESP32_HOST=192.168.1.105 pytest tests/test_esp32_bridge.py -m hil -v
   ```

On a timeout or disconnect, the proxy reconnects, re-sends `INIT_CONFIG` and retries. If the board
still can't answer, that call is computed by the identical local MC-DDI, so the simulation never hangs.

---

## 5. Design Notes
* **One set of route rules.** `src/models/route_semantics.py` defines how routes are read: a
  passenger boards at the first stop at their origin and gets off at the first stop at their
  destination after that, and stops that serve nobody are pruned. The engine, the dispatchers and the
  ESP32 port all use these same rules, so a dispatcher's predicted arrival times match the simulator
  (median error 0.00 s).
* **Clock.** The clock ticks in fixed `dt` steps, but within a tick each car is advanced to the exact
  call time before the call is dispatched. Event timestamps therefore don't depend on `dt`; this was
  verified with `dt` = 0.05, 0.1 and 0.37 s.
* **Hybrid weight projection.** The projection starts from the load-sensor reading and adds
  `avg_weight_kg` for each expected boarding. It is checked against `max_weight × (1 − buffer)` at every
  stop where the new passenger is aboard. Hidden true weights are never visible to a dispatcher.
* **Proxy pattern.** `ESP32DispatcherProxy` implements `IDispatcher`, so the engine can't tell local
  from remote. The newline-JSON protocol is `INIT_CONFIG`/`INIT_ACK`, `ALLOCATE_REQ`/`ALLOCATE_RES`,
  `ERROR` and `PING`/`PONG`; `BLEGattTransport` is a pluggable stub.

---

## 6. Experimental Results & Conclusion (ToDo #12)
`simulation_config.json`, `random_seed = 42`. Three cars (two 8-person/640 kg cars at 2 floors/s, one
10-person/800 kg car at 2.5 floors/s), 18 floors (B2–15). The data comes from `python -m src.benchmark --esp32-mock`;
latencies were measured on the development machine and vary slightly between runs.

### Full day: 200 calls, 3 traffic phases, E1 vibration fault 1200–1320 s, 3 % door obstructions
| Algorithm | Execution Mode | Avg Journey Time (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Avg Dispatch Latency (ms) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **FCFS** (baseline) | Local PC | 21.65 | 8.18 | 37.39 | 388 | 0.006 |
| **SCAN** (baseline) | Local PC | 21.03 | **5.16** | **24.58** | **355** | 0.015 |
| **MC-DDI** (ours) | Local PC | **18.94** | 5.42 | 31.66 | 378 | 0.031 |
| **MC-DDI** (ours) | ESP32 firmware, host-side mock over TCP | **18.94** | 5.42 | 31.66 | 378 | 0.101 (TCP round trip) |
| **MC-DDI** (ours) | ESP32 board over Wi-Fi | not measured yet (no hardware available); run §4.7 | | | | |

### Per traffic phase (each phase run alone for 20 min)
| Scenario | Avg journey FCFS / SCAN / **MC-DDI** (s) | Total stops FCFS / SCAN / **MC-DDI** |
| :--- | :---: | :---: |
| Morning up-peak (120 calls) | 23.80 / 24.80 / **20.08** | 219 / 187 / 203 |
| Midday mixed (60 calls) | 19.56 / 18.37 / **17.03** | 118 / 111 / 116 |
| Evening down-peak (100 calls) | 22.82 / 22.21 / **19.34** | 189 / 176 / 180 |

Robustness across seeds (from `outputs/benchmarks/benchmark_results.md`): MC-DDI has the lowest average
journey time on all 5 seeds tested (42, 1, 2, 3, 7) for both up-peak and full day.

### Conclusion
* **MC-DDI minimizes the stated objective.** Average journey time is **9.9 % lower than SCAN and 12.5 %
  lower than FCFS** over the full day, and **19 % lower than SCAN** in the morning up-peak. It wins in
  every scenario at seed 42 and on every seed tested for up-peak and full day.
* **It also makes fewer stops than FCFS** in every scenario. Merging passengers headed for the same
  floor removes redundant stops.
* **Honest trade-off against SCAN.** SCAN makes **fewer stops** (355 vs 378 over the full day, 187 vs 203
  at up-peak) and has lower hall waits (average 5.16 s vs 5.42 s, maximum 24.58 s vs 31.66 s). SCAN merges each call into the car
  that can pick it up soonest, which saves stops but makes passengers ride longer. MC-DDI optimizes
  total journey time, so it accepts an extra stop whenever that costs existing riders less than the
  alternative. Following the spec faithfully, we did **not** tune MC-DDI to win the stop count.
  The regression test `test_bench_mc_ddi_stops_not_above_scan_on_up_peak` is kept as a strict, documented
  `xfail` so this trade-off stays visible.
* **Fairness holds.** MC-DDI's maximum hall wait (31.7 s) stays well under the 60 s soft cap, and no
  passenger is starved. Both the vibration fault on E1 (no new assignments to it during the fault window)
  and runtime overloads were handled without losing a single call.
* **Edge feasibility.** The ESP32 firmware reproduces every MC-DDI decision bit for bit on the host,
  where a full TCP round trip averages 0.10 ms. On-board timing is not measured yet (no hardware
  available); the firmware logs `compute_ms` for every call, and §4.7 runs the latency test.

---

## 7. Test Suite (testing strategy Layers 1–5)
**Final status:** 248 passed, 1 xfailed (documented trade-off), 1 skipped (opt-in hardware test).

| File | Tests | Covers |
| :--- | ---: | :--- |
| `test_models_and_config.py` | 59 | DTOs and enums, plus fail-fast validation of every config rule |
| `test_request_generator.py` | 37 | All 5 generator invariants, 3- and 4-column CSV compatibility, both arrival modes, CLI |
| `test_simulation_engine.py` | 36 | Kinematics, no-stop point, door obstruction (+4.0 s exactly), overload recovery, vibration failover, output CSV/JSON validation |
| `test_dispatchers.py` | 61 | RouteEvaluator, insert/merge, weight feasibility, MC-DDI / SCAN / FCFS, fallback when all cars are full, benchmark regression, CLI |
| `test_gui.py` | 16 | Replay capture, rendering, charts, headless Streamlit app test, zero GUI imports in the core |
| `test_esp32_bridge.py` | 39 | Protocol round trips, TCP timeouts and disconnects, 50-request bit-for-bit parity, 400 random-state parity, constant memory, MicroPython-compatibility scan, opt-in HIL test |
| `test_scaffold.py` | 2 | Package layout |
