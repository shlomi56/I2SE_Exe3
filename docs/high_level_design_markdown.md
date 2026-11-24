# High-Level Design (HLD) – Smart Elevator Allocation System
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #3)  
**Architecture Pattern:** Decoupled Client–Controller / Hardware-in-the-Loop (HIL)  

---

## 1. Architectural Vision & Design Principle
To support both rapid software-only simulation (for benchmarking algorithms such as FCFS, SCAN, and Destination Dispatch) and live embedded deployment on an **ESP32 microcontroller**, the system employs a **decoupled Client–Controller architecture**.

The core architectural boundary isolates the **Allocation Brain** behind a strict interface (`IDispatcher`). The PC Simulator interacts with this interface identically whether the optimization algorithm executes locally in memory or remotely on the ESP32 over Wi-Fi or Bluetooth Low Energy (BLE).

---

## 2. System Architecture Diagram

```text
+-------------------------------------------------------------------------+
|                           PC HOST (SIMULATOR)                           |
|                                                                         |
|  +----------------------+      +-------------------------------------+  |
|  | Config & Input Mgr   |      |         Presentation Layer          |  |
|  | - Building JSON      |      | - Live Building GUI                 |  |
|  | - Request Generator  |      | - Metrics Dashboard & Alerts        |  |
|  +----------+-----------+      +------------------^------------------+  |
|             |                                     | (Observer Events)   |
|             v                                     |                     |
|  +------------------------------------------------+------------------+  |
|  |                    Simulation Engine (Core)                       |  |
|  |  - Discrete-Event / Hybrid Clock  - Kinematics & Door Physics     |  |
|  |  - Building & Elevator Fleet      - Virtual Sensor Suite          |  |
|  |  - Passenger Lifecycle & Overload Recovery Loop                   |  |
|  +----------+-------------------------------------+------------------+  |
|             |                                     |                     |
|             v (Allocation Request + State)        v (Logs & Stops)      |
|  +----------+--------------------+     +----------+------------------+  |
|  |     IDispatcher Interface     |     |   Logger & Analytics Mgr    |  |
|  +-------+---------------+-------+     | - Allocation & Stop CSV/Log |  |
|          |               |             | - Wait/Journey Time KPIs    |  |
|          v               v             +-----------------------------+  |
|  +---------------+ +------------------+                                 |
|  | Local         | | ESP32 HIL Proxy  |                                 |
|  | Allocator     | | (Client Adapter) |                                 |
|  | (FCFS/SCAN/DD)| +--------+---------+                                 |
|  +---------------+          |                                           |
+-----------------------------|-------------------------------------------+
                              | Wi-Fi (TCP/WebSocket) or BLE (GATT)
                              v
+-----------------------------|-------------------------------------------+
|                     ESP32 MICROCONTROLLER (EDGE)                        |
|                             |                                           |
|                  +----------+----------+                                |
|                  | Wireless Comms Mgr  |                                |
|                  | (Wi-Fi / BLE Stack) |                                |
|                  +----------+----------+                                |
|                             |                                           |
|                  +----------v----------+                                |
|                  |  Protocol Codec     |                                |
|                  |  (Lightweight JSON) |                                |
|                  +----------+----------+                                |
|                             |                                           |
|      +----------------------+----------------------+                    |
|      v                                             v                    |
|  +---+--------------------+            +-----------+-----------------+  |
|  | Fleet State Mirror     |<---------->| Core Allocation Engine      |  |
|  | (Digital Twin in SRAM) |            | (Marginal-Cost DD Optimizer)|  |
|  +------------------------+            +-----------------------------+  |
+-------------------------------------------------------------------------+
```

---

## 3. PC Host Components (Simulator)

### 3.1 Configuration & Input Manager
* Parses the standardized simulation parameters JSON (building floor range, elevator kinematics, capacity/weight limits, sensor thresholds, and traffic distributions).
* Houses the **Request Auto-Generator**, which consumes the JSON schema to produce reproducible chronological passenger call streams `(time, src, target)` alongside hidden ground-truth passenger weights for realistic load sensor simulation.

### 3.2 Simulation Engine (Physics & World State)
Acts as the single source of truth for the simulated building:
* **Clock & Event Loop:** Advances simulation time and schedules kinematic and passenger events.
* **Kinematics & Door Physics:** Models acceleration, cruising speed, deceleration, braking distance (point-of-no-return), door opening/closing delays, and passenger boarding/alighting dwell times.
* **Virtual Sensor Suite:** Emits real-time cabin telemetry—vertical position, speed, live weight load, vibration anomaly flags, and door obstruction events.
* **Overload Recovery Controller:** Detects when a boarding passenger causes the simulated load sensor to exceed $W_{\max, e}$, holds the doors open, marks the cabin `AT_CAPACITY`, and re-injects the bumped passenger into the dispatcher as a priority re-dispatch.

### 3.3 Dispatcher Interface (`IDispatcher`)
Applies the **Strategy / Adapter** design pattern to decouple simulation physics from decision-making:
* **Local Allocator:** Executes baseline strategies (FCFS, SCAN) and the Destination Dispatch Marginal-Cost Optimizer in-process for fast benchmarking and automated testing.
* **ESP32 HIL Proxy:** Serializes incoming call requests and live fleet sensor snapshots into compact payloads, transmits them to the ESP32 over Wi-Fi or BLE, and awaits the returned `elevator_id`.

### 3.4 Presentation Layer (GUI)
* Strictly decoupled from the Simulation Engine via an Observer/Event Bus pattern.
* Renders elevator shafts, real-time cabin positions, waiting queues with destination tags, sensor fault/overload alerts, and live total/average journey-time metrics.
* Decoupling enables headless batch execution for automated testing and performance benchmarking.

### 3.5 Logger & Analytics Manager
* Records every allocation assignment and every physical stop event.
* Exports the standardized output file matching the time-based input request format, augmented with the assigned `elevator_id` and explicit stop-by-stop entries per elevator.

---

## 4. ESP32 Microcontroller Components (Edge Node)

Designed to operate reliably within embedded constraints (limited SRAM, deterministic low-latency execution):

### 4.1 Wireless Comms Manager
* Manages the Wi-Fi (persistent TCP socket or WebSocket server) or BLE (GATT server characteristics) link with the PC host.
* Handles connection lifecycle, message framing, timeouts, and heartbeat keep-alives.

### 4.2 Protocol Codec
* Lightweight, bounded-memory JSON serializer/deserializer (e.g., `ArduinoJson` in C++ or `ujson` in MicroPython).
* Decodes incoming `ALLOC_REQ` and `STATE_SYNC` frames and encodes `ALLOC_RES` replies containing the assigned `elevator_id` and computation latency.

### 4.3 Fleet State Mirror (Digital Twin)
* Maintains a compact, fixed-size in-memory representation of every elevator's current state vector:
  $$S_e(t) = \big(p_e(t),\; \delta_e(t),\; \mathcal{Q}_e(t),\; \mathcal{A}_e(t),\; W_{\text{sensor}, e}(t),\; \sigma_e(t)\big)$$
* Synchronized via state snapshots from the PC Simulator so the ESP32 does not require full historical logs.

### 4.4 Core Allocation Engine
* Executes the online Destination Dispatch optimization algorithm on the microcontroller.
* Performs feasibility filtering (health flags, braking distance, proactive hybrid weight projection $\hat{W}_e(k) \le W_{\max, e} - W_{\text{buffer}}$) and selects the elevator minimizing marginal system journey time $\Delta C(e, r_i)$.

---

## 5. End-to-End Interaction Flows

### 5.1 Flow A: Standard Destination Dispatch Call
1. The Simulation Engine reaches timestamp $t_i$ for request $r_i = (t_i, s_i, d_i)$.
2. The Engine invokes `IDispatcher.allocate(r_i, fleet_telemetry)`.
3. *(HIL Mode)* The ESP32 HIL Proxy transmits:
   ```json
   {"type": "ALLOC_REQ", "req": {"id": 42, "time": 12.5, "src": 1, "target": 8}, "fleet_state": [...]}
   ```
4. The ESP32 updates its Fleet State Mirror, runs the Marginal-Cost Optimizer, and replies:
   ```json
   {"type": "ALLOC_RES", "req_id": 42, "elevator_id": 2, "compute_ms": 1.4}
   ```
5. The Simulation Engine assigns $r_i$ to Elevator 2, notifies the GUI, and writes the event to the Logger.

### 5.2 Flow B: Real-Time Sensor Overload Recovery
1. Elevator 2 arrives at $s_i = 1$; Passenger 42 boards, causing the Virtual Weight Sensor to report $W_{\text{sensor}, 2} > W_{\max, 2}$.
2. The Simulation Engine activates the door-hold safety interlock, bumps Passenger 42 back to the floor queue, and sets Elevator 2's status flag to `AT_CAPACITY`.
3. The Engine immediately invokes `IDispatcher.allocate(r_42_redispatch, updated_fleet_telemetry)` preserving the original call timestamp $t_{42}$ so accumulated wait time is accurately penalized.
4. The Dispatcher excludes Elevator 2 and assigns Passenger 42 to the next optimal elevator.

---

## 6. Key Architectural Trade-Offs

| Design Decision | Options Evaluated | Recommended Choice & Rationale |
| :--- | :--- | :--- |
| **PC $\leftrightarrow$ ESP32 Link** | Wi-Fi (TCP/WebSocket) vs. BLE (GATT) | **Wi-Fi (Persistent TCP or WebSocket).** BLE imposes strict MTU limits ($20\text{--}512\text{ bytes}$) requiring manual chunking for fleet state JSONs and adds OS-level pairing overhead. Wi-Fi handles full JSON frames natively with sub-$10\text{ ms}$ local latency. Keep the Transport interface abstract so BLE remains pluggable. |
| **Simulation Clock** | Fixed Time-Step ($\Delta t = 0.1\text{ s}$) vs. Pure Discrete-Event | **Hybrid (Discrete-Event core + Fixed-Tick GUI sampling).** Discrete-event scheduling guarantees exact kinematic timestamps and fast headless batch runs, while fixed-rate sampling smoothly interpolates cabin positions in the GUI. |
| **ESP32 Firmware Stack** | C++ (Arduino / ESP-IDF) vs. MicroPython | **C++ (Arduino / PlatformIO)** for predictable real-time performance and low memory footprint, or **MicroPython** to share nearly identical algorithm code with a Python PC simulator. |