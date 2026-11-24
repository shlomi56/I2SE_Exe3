# Statement of Work (SOW)
**Project Title:** End-to-End Design of a Smart Elevator System (Ex3)  
**Course:** Introduction to Software Engineering (OOP / OOD)  
**Document Type:** Project Execution Plan & Deliverable Specification  

---

## 1. Project Overview & Objectives
The objective of this project is to architect, implement, validate, and document a complete **Smart Elevator Allocation System** utilizing Destination Dispatch (DD) technology. The project bridges Object-Oriented Software Design, Discrete-Event/Time-Stepped Simulation, Combinatorial Online Optimization, and Embedded Systems Engineering (ESP32).

The primary engineering goal is to minimize the **total waiting time** (measured from initial hall call registration to destination floor arrival) across multi-elevator buildings under dynamic, real-time passenger arrivals.

---

## 2. Scope of Work & Work Breakdown Structure (WBS)

### Phase 1: Repository Setup & Mathematical Problem Formulation
* **Task 1.1 – Repository Initialization:** Establish a structured Git repository with clear separation for documentation, simulation engine, optimization algorithms, tests, and ESP32 firmware.
* **Task 1.2 – Formal Optimization Definition:** Mathematically formulate the online Destination Dispatch problem, defining decision variables, state transition dynamics, capacity/kinematic constraints, and the global objective function minimizing cumulative call-to-destination time.

### Phase 2: System Architecture (HLD & LLD)
* **Task 2.1 – High-Level Design (HLD):** Define system modules, data flows, and the hardware-in-the-loop boundary separating the PC Simulator from the ESP32 Allocator.
* **Task 2.2 – Low-Level Design (LLD):** Produce UML class diagrams and detailed class specifications (`Building`, `Elevator`, `SensorSuite`, `Request`, `TrafficGenerator`, `Simulator`, `AllocatorInterface`, `ESP32Bridge`, `Visualizer`), including public method signatures, parameters, and return types.

### Phase 3: Simulation Environment & Traffic Generation
* **Task 3.1 – JSON Parameter Schema:** Define and standardize the JSON configuration schema governing building dimensions, elevator physics, and traffic patterns.
* **Task 3.2 – Time-Based Request Auto-Generator:** Implement a configurable request generator producing `(time, src, target)` datasets for diverse scenarios (morning up-peak, evening down-peak, lunch-hour two-way traffic, and uniform random loads).
* **Task 3.3 – Core Building Simulator & GUI:** Build the simulation engine tracking elevator kinematics, sensor telemetry, and passenger lifecycles, paired with an interactive GUI visualizing real-time elevator movements and waiting-time metrics.
* **Task 3.4 – Standardized Stop & Allocation Logging:** Implement the output exporter generating the augmented request list and granular stop-by-stop logs tagged with `elevator_id`.

### Phase 4: Allocation Algorithm & ESP32 Embedded Deployment
* **Task 4.1 – Destination Dispatch Allocation Algorithm:** Design and implement the online scheduling heuristic/optimization algorithm (e.g., marginal-cost insertion / dynamic sectoring / predictive grouping) and benchmark it against baseline strategies (FCFS, SCAN).
* **Task 4.2 – Wireless Communication Layer:** Implement a reliable communication bridge between the PC simulator and the ESP32 microcontroller over Wi-Fi (TCP/WebSockets) or BLE.
* **Task 4.3 – ESP32 Firmware Implementation:** Port and deploy the allocation engine onto the ESP32, enabling real-time reception of `(time, src, target)` calls and state updates from the PC simulator and transmission of assigned elevator IDs.

### Phase 5: Verification, Testing & Final Report
* **Task 5.1 – Testing Strategy & Execution:** Execute unit tests (kinematics, capacity limits, cost functions), integration tests (PC-to-ESP32 round-trip communication), and stress tests (high-density rush-hour traffic).
* **Task 5.2 – Experimental Evaluation & Conclusion:** Analyze simulation results comparing total/average waiting times and stop counts across algorithms, and compile the final engineering report.

---

## 3. Deliverables Checklist

| ID | Deliverable | Format / Artifact | Acceptance Criteria |
| :--- | :--- | :--- | :--- |
| **D1** | **Source Code Repository** | Git Repository | Clean directory structure, README with build/run instructions, and modular OOP code. |
| **D2** | **Optimization Formulation** | Markdown / PDF Document | Complete mathematical model of states, constraints, and total waiting time objective. |
| **D3** | **HLD & LLD Documentation** | Architecture Doc + UML Diagrams | Clear component boundaries (PC vs. ESP32) and full class/method signatures. |
| **D4** | **Simulation Config & Generator** | `config.json` + Generator Module | Valid JSON schema; generates reproducible `(time, src, target)` request streams. |
| **D5** | **Building Simulator & GUI** | Executable Application | Accurately simulates elevator physics, visualizes cars/queues, and logs every stop with `elevator_id`. |
| **D6** | **ESP32 Firmware & Bridge** | C++/MicroPython Firmware + PC Client | Live Wi-Fi/BLE connection; ESP32 computes and returns valid elevator allocations in real time. |
| **D7** | **Test Suite & Final Report** | Automated Tests + Conclusion Doc | Passing unit/integration tests; empirical benchmark summary of algorithm performance. |

---

## 4. Technical & Architectural Assumptions
1. **Hardware Availability:** Development and testing assume access to an ESP32 development board and a host PC supporting Wi-Fi or BLE connectivity.
2. **Communication Reliability:** Both devices operate on the same local wireless network (if using Wi-Fi) or within reliable BLE radio range ($< 5\text{ meters}$) during hardware-in-the-loop runs.
3. **Simulation Fidelity:** Elevator kinematics assume deterministic acceleration/deceleration profiles unless stochastic sensor noise/obstruction events are explicitly enabled in the JSON configuration.

---

## 5. Verification & Acceptance Criteria
The project shall be deemed complete when:
1. The simulator runs end-to-end using the agreed-upon JSON configuration and auto-generated request list.
2. The GUI visually reflects real-time elevator allocation, movement, and passenger boarding/alighting.
3. The output file accurately records all allocations and individual elevator stops in the specified format.
4. The ESP32 successfully connects via Wi-Fi or BLE, receives live requests from the PC simulator, and drives the allocation decisions without simulation crashes or deadlocks.
5. Quantitative benchmarks demonstrate measurable reduction in total waiting time compared to non-destination-dispatch baselines (such as FCFS or standard SCAN).