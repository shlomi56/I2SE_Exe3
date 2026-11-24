# Low-Level Design (LLD) – Smart Elevator Allocation System
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #4)  
**Design Paradigm:** Object-Oriented Design (Strategy, Adapter/Proxy, Observer Patterns)  

---

## 1. Core Enumerations & Value Objects (Data Models)

### 1.1 Enumerations
* **`Direction`:** `UP`, `DOWN`, `IDLE`
* **`ElevatorState`:** `IDLE`, `ACCELERATING`, `CRUISING`, `DECELERATING`, `DOOR_OPENING`, `DWELLING`, `DOOR_CLOSING`, `OVERLOADED`, `OUT_OF_SERVICE`
* **`RequestStatus`:** `PENDING`, `ASSIGNED`, `BOARDED`, `COMPLETED`, `BUMPED_OVERLOAD`

### 1.2 Data Transfer Objects (DTOs)

| Class | Key Attributes | Description |
| :--- | :--- | :--- |
| **`BuildingConfig`** | `min_floor: int`, `max_floor: int`, `elevators: List[ElevatorConfig]`, `avg_passenger_weight_kg: float`, `weight_buffer_ratio: float`, `traffic_profile: dict` | Parsed directly from the standardized simulation JSON file. |
| **`ElevatorConfig`** | `elevator_id: int`, `max_speed: float`, `start_time: float`, `stop_time: float`, `door_open_time: float`, `door_close_time: float`, `dwell_time: float`, `max_weight_kg: float`, `max_capacity: int` | Static physical and kinematic parameters for a single elevator car. |
| **`Request`** | `req_id: int`, `timestamp: float`, `src_floor: int`, `target_floor: int`, `actual_weight_kg: float`, `assigned_elevator_id: Optional[int]`, `board_time: Optional[float]`, `arrival_time: Optional[float]`, `status: RequestStatus` | Represents a Destination Dispatch call `(time, src, target)` plus hidden ground-truth weight for the physics engine. |
| **`SensorSnapshot`** | `elevator_id: int`, `timestamp: float`, `position: float`, `speed: float`, `current_weight_kg: float`, `vibration_level: float`, `door_obstructed: bool`, `is_operational: bool`, `is_at_capacity: bool` | Immutable telemetry frame read from the cabin's sensors and transmitted to the dispatcher/ESP32. |
| **`AllocationDecision`** | `req_id: int`, `elevator_id: int`, `planned_route: List[int]`, `estimated_cost: float`, `compute_time_ms: float` | Output returned by any `IDispatcher` implementation. |
| **`StopRecord`** | `timestamp: float`, `elevator_id: int`, `floor: int`, `boarded_req_ids: List[int]`, `alighted_req_ids: List[int]` | Logged every time an elevator executes a physical stop. |

---

## 2. Simulation & Domain Classes (PC Core)

### 2.1 `SensorSuite`
Encapsulates real-time cabin monitoring and simulates hardware sensor readings (position, speed, weight load, vibrations, and door obstructions).
* **Attributes:** `max_weight_kg: float`, `vibration_threshold: float`, `door_obstructed: bool`, `vibration_level: float`, `fault_active: bool`
* **Public Methods:**
  * `read_snapshot(elevator_id: int, timestamp: float, position: float, speed: float, true_cabin_weight: float, at_capacity: bool) -> SensorSnapshot` — Packages live state into an immutable telemetry frame.
  * `is_overloaded(true_cabin_weight: float) -> bool` — Returns `True` if `true_cabin_weight > max_weight_kg`.
  * `is_safe_to_operate() -> bool` — Returns `False` if `fault_active` is set or `vibration_level > vibration_threshold`.
  * `inject_door_obstruction(obstructed: bool) -> None` — Simulates a passenger or object blocking the doors.
  * `inject_vibration_fault(level: float) -> None` — Simulates a mechanical anomaly for safety exclusion testing.

### 2.2 `Elevator`
Models a single smart elevator's kinematics, stop queue, onboard passengers, and door/overload state machine.
* **Attributes:** `config: ElevatorConfig`, `position: float`, `speed: float`, `direction: Direction`, `state: ElevatorState`, `stop_queue: List[int]`, `onboard_requests: List[Request]`, `assigned_waiting_requests: List[Request]`, `sensors: SensorSuite`, `at_capacity_flag: bool`
* **Public Methods:**
  * `assign_request(request: Request, updated_route: List[int]) -> None` — Commits a request and updates `stop_queue`.
  * `step(dt: float, current_time: float) -> List[Request]` — Advances physics by `dt` seconds (motion, door cycle, alighting, boarding). Returns any `Request` objects **bumped due to weight overload** for immediate re-dispatch.
  * `get_telemetry(current_time: float) -> SensorSnapshot` — Queries `SensorSuite` for the latest state snapshot.
  * `get_current_true_weight() -> float` — Sums `actual_weight_kg` of all `onboard_requests`.
  * `get_braking_floor() -> int` — Computes the nearest floor at which the car can safely stop given its current `position` and `speed` (point-of-no-return).

### 2.3 `Building`
Aggregates the elevator fleet and floor-level waiting areas.
* **Attributes:** `config: BuildingConfig`, `elevators: Dict[int, Elevator]`, `floor_queues: Dict[int, List[Request]]`
* **Public Methods:**
  * `get_elevator(elevator_id: int) -> Elevator` — Retrieves a specific elevator car by ID.
  * `get_fleet_telemetry(current_time: float) -> List[SensorSnapshot]` — Collects live telemetry snapshots across all elevators.
  * `add_waiting_passenger(request: Request) -> None` — Places a passenger in the queue at `request.src_floor`.
  * `is_valid_floor(floor: int) -> bool` — Validates `min_floor <= floor <= max_floor`.

---

## 3. Allocation & Strategy Layer (Shared PC & ESP32 Logic)

### 3.1 `RouteEvaluator` (Utility / Helper Class)
Provides deterministic kinematic time estimation and proactive capacity verification so all dispatchers share identical physics math.
* **Public Methods:**
  * `estimate_leg_time(config: ElevatorConfig, start_floor: float, end_floor: int) -> float` — Computes $t_{\text{start}} + \frac{|d|}{v} + t_{\text{stop}}$.
  * `check_weight_feasibility(snapshot: SensorSnapshot, config: ElevatorConfig, active_reqs: List[Request], candidate_req: Request, route: List[int], avg_weight: float, buffer_ratio: float) -> bool` — Evaluates the proactive hybrid weight constraint across every stop $k$ in `route`.
  * `compute_marginal_cost(snapshot: SensorSnapshot, config: ElevatorConfig, active_reqs: List[Request], candidate_req: Request, candidate_route: List[int], current_time: float) -> float` — Computes $\Delta C(e, r_i)$ (new passenger journey time plus added delay to all existing passengers).

### 3.2 `IDispatcher` (Abstract Interface — Strategy Pattern)
Decouples `SimulationEngine` from how and where allocation decisions are computed.
* **Public Methods:**
  * `initialize(building_config: BuildingConfig) -> None` — Passes static building and elevator specifications once at startup.
  * `allocate(request: Request, fleet_snapshots: List[SensorSnapshot], active_assignments: Dict[int, List[Request]]) -> AllocationDecision` — Selects the optimal elevator and route for `request` online.
  * `notify_state_update(fleet_snapshots: List[SensorSnapshot]) -> None` — Optional periodic or event-driven telemetry synchronization (used by the ESP32 digital twin).

### 3.3 Concrete `IDispatcher` Implementations
* **`FCFSDispatcher`:** Assigns to the earliest-available elevator, appending `(src, target)` to the end of its queue (baseline).
* **`SCANDispatcher`:** Uses directional sweep (elevator algorithm) to insert stops along the current travel direction (baseline).
* **`MarginalCostDDDispatcher`:** Implements the Destination Dispatch optimization algorithm from ToDo #2 using `RouteEvaluator` to minimize $\Delta C(e, r_i)$.
* **`ESP32DispatcherProxy` (Adapter/Proxy Pattern):**
  * **Attributes:** `transport: ITransport`, `timeout_ms: int`
  * **Public Methods:** Implements `IDispatcher` by serializing `(request, fleet_snapshots)` into JSON, transmitting it over `transport`, and deserializing the ESP32's `AllocationDecision`.

### 3.4 `ITransport` (Abstract Interface — Communication Layer)
* **Public Methods:**
  * `connect(address: str, port_or_uuid: str) -> bool`
  * `send_and_receive(payload: str, timeout_ms: int) -> str`
  * `disconnect() -> None`
* **Concrete Implementations:** `WiFiTCPTransport`, `BLEGattTransport`

---

## 4. Simulation Control, Generator & Observers

### 4.1 `RequestGenerator`
Auto-generates time-based passenger calls from the simulation JSON configuration.
* **Public Methods:**
  * `generate(config: BuildingConfig, seed: Optional[int] = None) -> List[Request]` — Synthesizes time-ordered calls (e.g., morning up-peak, lunch inter-floor, evening down-peak) with realistic weight distributions.
  * `save_to_file(requests: List[Request], filepath: str) -> None` — Exports the `time, src, target` request list.
  * `load_from_file(filepath: str) -> List[Request]` — Loads a repeatable test scenario from disk.

### 4.2 `ISimulationObserver` (Abstract Interface — Observer Pattern)
* **Public Methods:**
  * `on_request_allocated(request: Request, decision: AllocationDecision) -> None`
  * `on_elevator_stop(stop_record: StopRecord) -> None`
  * `on_overload_detected(elevator_id: int, bumped_request: Request, timestamp: float) -> None`
  * `on_tick(current_time: float, fleet_snapshots: List[SensorSnapshot]) -> None`
  * `on_simulation_complete(summary_metrics: dict) -> None`

### 4.3 Concrete Observers
* **`SimulationLogger`:** Writes the required output file containing every allocated request and a dedicated line for each physical elevator stop (with `elevator_id`), and computes final KPIs (total/average journey time, hall wait time, max wait time).
* **`GUIRenderer`:** Visualizes building shafts, live cabin positions, waiting queues, overload alerts, and real-time metrics.

### 4.4 `SimulationEngine`
Orchestrates the simulation clock, request dispatch, physics ticks, and overload recovery loop.
* **Attributes:** `building: Building`, `dispatcher: IDispatcher`, `requests: List[Request]`, `observers: List[ISimulationObserver]`, `current_time: float`, `dt: float`
* **Public Methods:**
  * `register_observer(observer: ISimulationObserver) -> None`
  * `run(realtime_scale: float = 0.0) -> dict` — Executes the full simulation (`0.0` = max-speed headless mode; `1.0` = real-time GUI mode) and returns summary KPIs.
  * `step_once() -> bool` — Advances by one `dt` tick, dispatches due calls, calls `Elevator.step()`, re-dispatches any bumped overload requests immediately, and notifies observers.

---

## 5. ESP32 Firmware-Side Class Mapping

To keep SRAM usage predictable and avoid heap fragmentation on the microcontroller, the ESP32 mirrors the core allocation classes using fixed-size structs and arrays:

| ESP32 Class / Module | Responsibility | Key Public Methods |
| :--- | :--- | :--- |
| **`WirelessServer`** | Manages Wi-Fi TCP socket or BLE GATT server. | `begin(ssid, pass)`, `poll_client()`, `send_response(json_str)` |
| **`ProtocolCodec`** | Parses incoming JSON (`ALLOCATE_REQ`, `INIT_CONFIG`) and serializes `ALLOCATE_RES`. | `decode_config(json, BuildingConfig&)`, `decode_call(json, Request&, SensorSnapshot[])`, `encode_decision(AllocationDecision)` |
| **`FleetStateMirror`** | Maintains fixed-size arrays of elevator configs, current telemetry, and active stop queues in SRAM. | `load_config(config)`, `update_telemetry(snapshots[])`, `commit_assignment(decision)` |
| **`EdgeAllocator`** | Runs the exact same Marginal-Cost Destination Dispatch logic as `MarginalCostDDDispatcher`. | `allocate(request, fleet_mirror) -> AllocationDecision` |

---

## 6. Class Relationship Overview (UML Summary)

```text
BuildingConfig --(parsed by)--> RequestGenerator --(produces)--> Request
      |                                                             |
      v                                                             v
   Building *--(1..m)--> Elevator *--(1)--> SensorSuite       SimulationEngine
                              |                  |                  |
                              |                  v                  +--(notifies)--> ISimulationObserver
                              |            SensorSnapshot           |                   +-- SimulationLogger
                              |                  |                  |                   +-- GUIRenderer
                              v                  v                  |
                       RouteEvaluator <--- IDispatcher <------------+
                                              ^
                     +------------------------+------------------------+
                     |                        |                        |
           MarginalCostDDDispatcher    FCFS / SCAN            ESP32DispatcherProxy
                                                                       |
                                                                 (uses ITransport)
                                                                       v
                                                            [ ESP32 WirelessServer ]
                                                                       |
                                                          ProtocolCodec + EdgeAllocator
```