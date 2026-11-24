# Software Requirements Specification (SRS)
**Project:** End-to-End Smart Elevator Allocation System (Ex3)  
**Course:** Introduction to Software Engineering (OOP / OOD)  
**Version:** 1.0  

---

## 1. Introduction

### 1.1 Purpose
This document specifies the functional, non-functional, interface, and hardware-software integration requirements for the **End-to-End Smart Elevator Allocation System**. It serves as the primary technical blueprint for system architecture, simulation development, optimization algorithm design, and ESP32 microcontroller deployment.

### 1.2 Scope
The system models and controls a multi-story building equipped with a bank of smart elevators operating under a **Destination Dispatch (DD)** paradigm. The scope spans three interconnected layers:
1. **Optimization Engine:** An online dispatching algorithm minimizing total passenger journey time (call-to-destination).
2. **Building Simulator & GUI:** A discrete-event or time-stepped simulation environment with configurable JSON parameters, synthetic traffic generation, visual state rendering, and structured stop logging.
3. **Embedded Hardware Node (ESP32):** Offloading the allocation decision-making logic onto an ESP32 microcontroller communicating in real time with the PC simulator over Wi-Fi or Bluetooth Low Energy (BLE).

### 1.3 Definitions, Acronyms, and Abbreviations
* **DD (Destination Dispatch):** An elevator control method where passengers register their target floor prior to boarding, allowing the controller to group passengers with identical or adjacent destinations.
* **Online Optimization:** Real-time decision-making where incoming requests are processed sequentially without prior knowledge of future arrivals.
* **Total Waiting Time ($T_{\text{total}}$):** Defined for this project as the elapsed time between the passenger initiating a call ($t_{\text{call}}$) and exiting at their target floor ($t_{\text{arrival}}$).
* **ESP32:** Low-power system-on-a-chip microcontroller with integrated Wi-Fi and dual-mode Bluetooth.

---

## 2. Overall Description

### 2.1 System Architecture & Perspective
The system follows a decoupled Client-Server / Hardware-in-the-Loop (HIL) architecture:
* **PC Simulator (Host):** Maintains the ground-truth physics and state of the building, elevators, virtual sensors, and passenger queues. Dispatches incoming call events and telemetry to the allocator.
* **Allocator Node (ESP32 / Software Interface):** Receives building state and new passenger requests `(time, src, target)`, computes the optimal elevator assignment $e_k \in E$, and returns the assigned `elevator_id`.

### 2.2 User Characteristics
* **System Engineers / Evaluators:** Configure building parameters via JSON, execute benchmark simulations (e.g., comparing FCFS, SCAN, and DD), inspect real-time GUI behavior, and validate output logs and hardware latency.

### 2.3 Constraints & Assumptions
* **Single-Assignment Commitment:** Once an elevator is assigned to a request (or once a passenger boards), allocation commitments must respect physical constraints (maximum passenger capacity/weight load, kinematic acceleration/deceleration limits).
* **Real-Time Responsiveness:** The ESP32 allocation round-trip time must not bottleneck the simulation clock.
* **Standardized Configuration:** Simulation parameters must adhere to a shared class-wide JSON schema.

---

## 3. Functional Requirements

### 3.1 Optimization & Allocation Engine (`FR-OPT`)
* **`FR-OPT-01` Online Request Processing:** Upon receiving a request $r_i = (t_i, s_i, d_i)$, the system shall immediately assign an elevator $e_k$ without waiting for future requests.
* **`FR-OPT-02` Objective Function Minimization:** The allocation algorithm shall select $e_k$ to minimize the global objective function:
  $$\min \sum_{i=1}^{N} (t_{\text{arrival}, i} - t_{\text{call}, i})$$
  accounting for marginal delay added to passengers already assigned to or inside $e_k$.
* **`FR-OPT-03` Destination Grouping:** The algorithm shall prioritize grouping passengers with identical or adjacent target floors into the same cabin to minimize total intermediate stops.
* **`FR-OPT-04` Capacity & Safety Enforcement:** The allocator shall exclude any elevator whose projected weight load or passenger count exceeds its configured maximum capacity, or whose sensors report a fault/door obstruction.

### 3.2 Building Simulation & Traffic Generation (`FR-SIM`)
* **`FR-SIM-01` JSON Configuration Loader:** The simulator shall parse a structured JSON file defining building geometry, elevator kinematics, sensor thresholds, and traffic distributions.
* **`FR-SIM-02` Time-Based Request Auto-Generator:** The system shall include a generator module that consumes the simulation JSON and outputs a deterministic or stochastic chronological list of requests formatted as `(time, src, target)`.
* **`FR-SIM-03` Physics & State Engine:** The simulator shall model elevator movement across floors, including acceleration, cruising speed, deceleration, door open/close dwell times, and passenger boarding/alighting durations.
* **`FR-SIM-04` Simulated Sensor Telemetry:** Each simulated elevator shall emit real-time sensor readings (current floor/position, velocity, weight load, vibration status, and door obstruction flags).
* **`FR-SIM-05` Graphical User Interface (GUI):** The simulator shall render a live visual interface displaying elevator shafts, cabin positions, passenger queues per floor, destination tags, and live performance metrics (e.g., average/total waiting time, completed trips).
* **`FR-SIM-06` Standardized Output Log:** Upon simulation completion, the system shall export a log matching the input request list format, augmented with the assigned `elevator_id` and explicit entries for every physical stop executed by each elevator.

### 3.3 ESP32 Embedded Hardware Integration (`FR-HW`)
* **`FR-HW-01` Wireless Communication Link:** The ESP32 shall establish a reliable bidirectional communication channel with the PC simulator using either **Wi-Fi (TCP/UDP/WebSockets)** or **Bluetooth Low Energy (BLE)**.
* **`FR-HW-02` State Synchronization & Dispatch:** The PC simulator shall transmit incoming `(time, src, target)` requests alongside current elevator state vectors to the ESP32, and the ESP32 shall execute the allocation algorithm and return the optimal `elevator_id`.
* **`FR-HW-03` Hardware Fallback / Standalone Verification:** The ESP32 firmware shall log allocation computation times and support serial debugging output.

---

## 4. External Interface Requirements

### 4.1 Simulation Configuration Schema (JSON)
The configuration file (`sim_config.json`) shall encapsulate:
* `building`: `num_floors`, `min_floor`, `floor_height_m`, `lobby_floor`
* `elevators`: `count`, `max_capacity_persons`, `max_weight_kg`, `max_speed_mps`, `acceleration_mps2`, `door_open_time_s`, `door_close_time_s`, `passenger_transfer_time_s`
* `traffic_profile`: `duration_s`, `arrival_rate_lambda`, `rush_hour_mode` (`up_peak`, `down_peak`, `inter_floor`, `mixed`), `random_seed`

### 4.2 PC-to-ESP32 Communication Protocol
Messages exchanged over Wi-Fi/BLE shall use lightweight serialized payloads (JSON or compact binary frames):
* **Request Payload (PC $\rightarrow$ ESP32):** `{"msg_type": "ALLOC_REQ", "req_id": 42, "time": 12.5, "src": 1, "target": 8, "elevators": [...]}`
* **Response Payload (ESP32 $\rightarrow$ PC):** `{"msg_type": "ALLOC_RES", "req_id": 42, "elevator_id": 2, "compute_us": 310}`

### 4.3 Input / Output File Formats
* **Input Request List:** `time, src, target`
* **Output Trace Log:** Preserves input columns while appending `elevator_id` and recording each stop event (`stop_time, elevator_id, floor, event_type, passengers_boarded, passengers_alighted`).

---

## 5. Non-Functional Requirements
* **`NFR-01` Modularity & OOP Principles:** The codebase shall strictly separate domain models (`Building`, `Elevator`, `Passenger`, `Request`), simulation mechanics (`Simulator`, `TrafficGenerator`), visualization (`GUI`), and allocation strategies (`AllocationStrategy` interface) to enable seamless swapping between local simulation and ESP32 hardware execution.
* **`NFR-02` Latency:** ESP32 allocation inference per request shall complete within $< 50\text{ ms}$ (excluding network jitter) to preserve real-time simulation responsiveness.
* **`NFR-03` Reproducibility:** Given an identical JSON configuration and random seed, the traffic generator and simulator shall produce identical deterministic results.
* **`NFR-04` Testability:** Core algorithmic and physics components shall support automated unit and integration testing independent of the GUI and physical microcontroller.