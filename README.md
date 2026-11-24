# Smart Elevator Allocation System (Ex3)
**Course:** Introduction to Software Engineering (OOP / OOD)  
**Scope:** End-to-End Online Destination Dispatch System — Optimization, Simulation, GUI, and ESP32 Hardware-in-the-Loop (HIL)

---

## 1. Project Overview
This repository contains the complete architecture, specifications, simulation engine, and embedded firmware for a **Smart Elevator Allocation System**. 

Given a multi-story building with a fleet of smart elevators, the system solves an **Online Multi-Vehicle Dial-a-Ride Problem (Online $m$-DARP)** under a **Destination Dispatch (DD)** paradigm. When passengers submit their target floor at a hall kiosk (`time, src, target`), the controller assigns an elevator in real time to minimize **total journey time** (hall waiting time plus in-cabin transit time) while strictly enforcing kinematic physics, capacity/weight limits, and real-time sensor safety interlocks.

### Core Features
* **Destination Dispatch (DD) with MC-DDI:** Uses **Marginal-Cost Destination Dispatch Insertion (MC-DDI)** to naturally group passengers heading to identical or adjacent floors, minimizing intermediate stops and outperforming traditional `FCFS` and `SCAN` baselines.
* **Real-Time Sensor Monitoring & Overload Recovery:** Models cabin position, speed, live weight load, vibration faults, and door obstructions. Combines **proactive hybrid weight projection** at dispatch time with a **reactive overload recovery loop** if actual boarding weight exceeds $W_{\max, e}$.
* **Decoupled Simulation Engine & GUI:** Supports both headless high-speed batch runs for benchmarking/testing and real-time graphical visualization of elevator shafts, waiting queues, and live KPIs.
* **ESP32 Hardware-in-the-Loop (HIL):** Isolates the allocation brain behind an `IDispatcher` interface so the exact same MC-DDI algorithm can run either locally on the PC or remotely on an **ESP32 microcontroller** over Wi-Fi (TCP/WebSockets) or BLE (GATT).

---

## 2. Repository Structure

```text
smart-elevator-system/
├── README.md                              # Project overview, setup, and navigation
├── requirements.txt                       # Python dependencies (simulator, GUI, pytest)
├── config/
│   └── simulation_config.json             # Standardized building, physics, sensor & traffic schema
├── docs/                                  # Complete engineering specifications
│   ├── SRS_Smart_Elevator_System.md       # Software Requirements Specification
│   ├── SOW_Smart_Elevator_System.md       # Statement of Work & WBS
│   ├── formal_optimization_problem.md     # ToDo #2: Mathematical problem formulation
│   ├── high_level_design.md               # ToDo #3: PC Simulator + ESP32 HIL architecture
│   ├── low_level_design.md                # ToDo #4: Classes, methods, parameters & UML
│   ├── allocation_method.md               # ToDo #5: MC-DDI pseudocode & complexity analysis
│   ├── simulation_config_docs.md          # ToDo #6: JSON parameter reference & validation
│   ├── request_generator_spec.md          # ToDo #7: CSV format & Poisson traffic generator spec
│   ├── testing_strategy.md                # ToDo #9: 5-layer verification & HIL test plan
│   └── output_format_spec.md              # ToDo #10: Output trace CSV & metrics JSON spec
├── data/                                  # Generated request streams & simulation outputs
│   ├── requests.csv                       # Auto-generated time-based calls (time, src, target)
│   ├── simulation_output.csv              # Allocated requests + per-stop elevator log
│   └── simulation_summary.json            # Final KPIs (journey time, wait time, stops, latency)
├── src/                                   # PC Simulator & Optimization Engine (Python)
│   ├── models/                            # Enums, DTOs (BuildingConfig, Request, SensorSnapshot)
│   ├── domain/                            # Building, Elevator, SensorSuite
│   ├── dispatchers/                       # IDispatcher, RouteEvaluator, MC_DDI, SCAN, FCFS, ESP32Proxy
│   ├── transport/                         # ITransport, WiFiTCPTransport, BLEGattTransport
│   ├── simulation/                        # SimulationEngine, SimulationLogger
│   ├── gui/                               # Decoupled live building visualizer (GUIRenderer)
│   ├── request_generator.py               # Standalone CLI/module for ToDo #7
│   └── main.py                            # Simulator entry point
├── esp32_firmware/                        # Embedded Edge Allocator (C++ / PlatformIO or MicroPython)
│   ├── src/                               # WirelessServer, ProtocolCodec, FleetStateMirror, EdgeAllocator
│   └── platformio.ini                     # Build & flash configuration
└── tests/                                 # Automated pytest suite (Unit, Integration, Benchmark, HIL)
    ├── test_kinematics_and_evaluator.py
    ├── test_request_generator.py
    ├── test_engine_and_sensors.py
    ├── test_benchmarks.py
    └── test_esp32_hil.py
```

---

## 3. Assignment Deliverables Map

| ToDo | Deliverable | Status | Artifact / Location |
| :--- | :--- | :--- | :--- |
| **#1** | Construct Repository | Spec Ready | `README.md` & directory layout |
| **#2** | Define Optimization Problem | Done | `docs/formal_optimization_problem.md` |
| **#3** | High-Level Design (PC + ESP32) | Done | `docs/high_level_design.md` |
| **#4** | Low-Level Design (Classes & Methods) | Done | `docs/low_level_design.md` |
| **#5** | Design Allocation Method (MC-DDI) | Done | `docs/allocation_method.md` |
| **#6** | Simulation Parameters JSON | Done | `config/simulation_config.json`, `docs/simulation_config_docs.md` |
| **#7** | Request List & Auto-Generator | Spec Done | `docs/request_generator_spec.md` $\rightarrow$ `src/request_generator.py` |
| **#8** | Complete Building Simulator & GUI | Pending Code | `src/` |
| **#9** | Testing Strategy & Test Suite | Spec Done | `docs/testing_strategy.md` $\rightarrow$ `tests/` |
| **#10** | Standardized Output & Stop Log | Spec Done | `docs/output_format_spec.md` $\rightarrow$ `data/simulation_output.csv` |
| **#11** | ESP32 Allocation & Wi-Fi/BLE Link | Pending Code | `esp32_firmware/`, `src/transport/` |
| **#12** | Experimental Conclusion & Results | Pending Run | Section 6 below |

---

## 4. Quick Start (Usage)

### 4.1 Installation
```bash
python -m venv .venv
source .venv/bin/activate      # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4.2 Generate Time-Based Requests (ToDo #7)
```bash
python -m src.request_generator \
    --config config/simulation_config.json \
    --output data/requests.csv \
    --seed 42
```
*(Add `--strict-3col` to emit a bare `time,src,target` file without ground-truth weights.)*

### 4.3 Run the Simulator (ToDo #8 & #10)
```bash
# Headless benchmark run (fast, exports simulation_output.csv & simulation_summary.json)
python -m src.main --config config/simulation_config.json --requests data/requests.csv --algorithm MC_DDI

# Live GUI run
python -m src.main --config config/simulation_config.json --requests data/requests.csv --algorithm MC_DDI --gui
```

### 4.4 Run the Automated Test Suite (ToDo #9)
```bash
pytest tests/ -v
```

---

## 5. ESP32 Hardware-in-the-Loop Setup (ToDo #11)
1. Flash `esp32_firmware/` onto your ESP32 board (configure your Wi-Fi SSID/password or BLE UUID inside the firmware config).
2. Note the ESP32's local IP address from the Serial Monitor (`115200` baud).
3. In `config/simulation_config.json`, set:
   ```json
   "esp32_bridge": {
     "enabled": true,
     "transport": "WIFI_TCP",
     "host": "<ESP32_IP_ADDRESS>",
     "port": 5000
   }
   ```
4. Launch the simulator normally; `ESP32DispatcherProxy` will automatically stream incoming calls and sensor snapshots to the ESP32 and execute its returned allocations in real time.

---

## 6. Experimental Results & Conclusion (ToDo #12)
*(Populate this table after running `pytest tests/test_benchmarks.py` on `config/simulation_config.json` with `random_seed = 42`.)*

| Algorithm | Execution Mode | Avg Journey Time (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Avg Dispatch Latency (ms) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **FCFS** (Baseline) | Local PC | — | — | — | — | — |
| **SCAN** (Baseline) | Local PC | — | — | — | — | — |
| **MC-DDI** (Ours) | Local PC | — | — | — | — | — |
| **MC-DDI** (Ours) | ESP32 (Wi-Fi) | — | — | — | — | — |