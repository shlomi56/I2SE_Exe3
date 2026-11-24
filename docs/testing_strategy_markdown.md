# Testing Strategy – Smart Elevator Allocation System
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #9)  
**Test Framework:** `pytest` (PC Simulator) + Hardware-in-the-Loop (HIL) Equivalence Harness (ESP32)  

---

## 1. Overview
To verify correctness across physical kinematics, online optimization, real-time sensor telemetry, and ESP32 wireless communication, testing is structured into five layers. All automated tests run deterministically using fixed random seeds from `simulation_config.json`.

---

## 2. Layer 1: Unit Tests (Isolated Component Verification)
Verify deterministic math and invariants without executing a full building simulation:

### 2.1 Kinematics & `RouteEvaluator`
* **Travel-Time Formula:** Confirm `estimate_leg_time()` matches $t_{\text{start}} + \frac{|d|}{v} + t_{\text{stop}}$ using `elevators[]` specs from `simulation_config.json`.
* **Point-of-No-Return:** Verify `get_braking_floor()` prevents physically impossible instantaneous stops when a cabin is already moving past or within braking distance of a floor.
* **Route Merging & Precedence:** Confirm `insert_or_merge()` deduplicates shared pickup/drop-off floors (incurring $0$ extra stops) and strictly maintains existing pickup-before-dropoff precedence.

### 2.2 Proactive Capacity & `SensorSuite`
* **Hybrid Weight Feasibility:** Verify `check_weight_feasibility()` rejects any candidate route where projected weight exceeds $W_{\max, e} \cdot (1 - \text{weight\_buffer\_ratio})$ at any future stop $k$.
* **Safety Interlocks:** Verify `is_safe_to_operate()` returns `False` whenever `vibration_level > max_vibration_threshold` or `fault_active == True`.

### 2.3 `RequestGenerator`
* Validate all 5 output invariants defined in `request_generator_spec.md`:
  1. Exact row count matching `total_requests` (in Mode 1).
  2. Monotonically non-decreasing timestamps within $[0.0, \text{duration\_sec}]$.
  3. Valid floor bounds with $\text{src} \neq \text{target}$.
  4. Byte-for-byte determinism across repeated runs with identical `random_seed`.
  5. Empirical phase ratios (`UP_PEAK`, `DOWN_PEAK`, `INTER_FLOOR`) within $\pm 3\sigma$ binomial tolerance.

### 2.4 Protocol Codec
* Verify round-trip JSON serialization and deserialization for `INIT_CONFIG`, `ALLOCATE_REQ`, and `ALLOCATE_RES`, including edge cases (negative basement floors, empty stop queues, and active fault flags).

---

## 3. Layer 2: Integration & Edge-Case Tests
Execute headless `SimulationEngine` runs (`realtime_scale = 0.0`) to validate end-to-end physics and failure recovery:

* **Global Conservation & Safety Invariants (Asserted on Every Run):**
  * $100\%$ of generated requests reach `COMPLETED` status with $\text{arrival\_time} > \text{board\_time} \ge \text{request.timestamp}$.
  * No elevator ever transitions into motion while $\text{current\_true\_weight} > W_{\max, e}$ or $|\text{onboard\_requests}| > C_e$.
* **Runtime Weight Overload Recovery:**
  * Inject a burst of heavy passengers (`weight_kg = 140.0`) targeting the same elevator.
  * Assert that the cabin triggers the overload safety interlock, bumps the excess passenger, sets `is_at_capacity = True`, immediately re-dispatches the passenger retaining their **original call timestamp**, and completes delivery on another car.
* **Vibration Fault Failover:**
  * Execute the `injected_faults` window from `simulation_config.json` (`elevator_id: 1`, `VIBRATION_FAULT` over $[1200.0\text{ s}, 1320.0\text{ s}]$).
  * Assert $0$ new allocations are assigned to Elevator 1 during $[1200.0, 1320.0]$ and normal allocation resumes after $1320.0\text{ s}$.
* **Door Obstruction Delay:**
  * Inject a deterministic door obstruction (`door_obstruction_delay_sec = 4.0`) and verify subsequent stop timestamps shift by exactly $4.0\text{ s}$.
* **All-Cars-Full Fallback:**
  * Saturate all elevators simultaneously and verify `fallback_tail_allocation()` queues incoming requests safely without dropping calls or raising exceptions.

---

## 4. Layer 3: Algorithmic Benchmarking & Regression Tests
Validate the primary optimization objective—minimizing total journey time—across identical seeded request streams:

* **Benchmark Scenarios:** Compare `FCFS`, `SCAN`, and `MC_DDI` across:
  1. Heavy Morning Up-Peak (`up_peak_ratio = 0.75`)
  2. Midday Mixed Inter-Floor (`inter_floor_ratio = 0.50`)
  3. Evening Down-Peak (`down_peak_ratio = 0.75`)
  4. Full 3-phase `simulation_config.json` profile
* **Acceptance Criteria:**
  * `MC_DDI` must achieve a **strictly lower average total journey time** and **fewer total elevator stops** than both `FCFS` and `SCAN` on the up-peak and full-day benchmarks.
  * Maximum hall waiting time under `MC_DDI` must remain bounded (confirming no passenger starvation).

---

## 5. Layer 4: ESP32 Hardware-in-the-Loop (HIL) & Equivalence Tests
Validate the embedded firmware over Wi-Fi (`WIFI_TCP`) or BLE (`BLE_GATT`):

* **Bit-for-Bit Decision Parity:** Replay a $50$-request trace through both `MarginalCostDDDispatcher` (local PC) and `ESP32DispatcherProxy` (ESP32 hardware). Assert $100\%$ identical `elevator_id` selections on every call.
* **Latency & Stability:** Over a continuous $200$-request run, assert $99\text{th}$-percentile round-trip latency $< 500\text{ ms}$ (`esp32_bridge.timeout_ms`) and zero MCU reboots or heap leaks.
* **Link-Drop Resilience:** Simulate a wireless disconnect/timeout mid-run and confirm `ESP32DispatcherProxy` logs the error and retries (or fails safely) without hanging the simulator.

---

## 6. Layer 5: Output Log Validation Tests
* Parse `simulation_output.csv` (ToDo #10) and assert:
  1. Strict schema compliance (`time,src,target,elevator_id,event_type`).
  2. Chronologically non-decreasing timestamps.
  3. $1:1$ correspondence between `ALLOC` rows and all input `(time, src, target)` requests.
  4. Every completed passenger's pickup (`src`) and drop-off (`target`) corresponds to a logged `STOP` entry for their assigned `elevator_id`.