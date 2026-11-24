# Simulation Parameters JSON Specification
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #6)  
**Schema Version:** `1.0`  
**Config File:** `simulation_config.json`  

---

## 1. Purpose
`simulation_config.json` serves as the standardized, class-wide configuration schema for the Smart Elevator Allocation System. It defines all physical building properties, elevator fleet kinematics, virtual sensor thresholds, optimization hyperparameters, synthetic traffic profiles, and ESP32 Hardware-in-the-Loop (HIL) communication settings so that any simulator, request generator, and allocator can run identical, reproducible benchmarks.

---

## 2. Parameter Reference & Validation Rules

### 2.1 `simulation`
Controls global clock behavior, horizon, and determinism.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | Non-empty | Human-readable scenario identifier used in logs and reports. |
| `duration_sec` | `float` | $> 0\text{ s}$ | Total time horizon for generating new passenger calls. The engine continues running past this time until all active requests are completed. |
| `time_step_dt_sec` | `float` | $0.05\text{--}0.5\text{ s}$ | Physics tick granularity. $0.1\text{ s}$ provides smooth GUI updates and accurate kinematic integration. |
| `random_seed` | `int` | Any integer | Seed for the random number generator to guarantee reproducible traffic streams and stochastic sensor events. |
| `realtime_scale` | `float` | $\ge 0.0$ | Execution speed multiplier ($0.0$ = headless max-speed batch mode; $1.0$ = real-time GUI playback). |

### 2.2 `building`
Defines the vertical geometry of the building.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `min_floor` | `int` | `< max_floor` | Lowest accessible floor (negative integers represent basement levels). |
| `max_floor` | `int` | `> min_floor` | Highest accessible floor. |
| `lobby_floor` | `int` | `[min_floor, max_floor]` | Primary entry/exit floor used by `up_peak` and `down_peak` traffic profiles. |
| `floor_height_meters` | `float` | $> 0\text{ m}$ | Physical floor-to-floor height used for optional metric scaling and GUI proportions. |

### 2.3 `elevators[]`
An array of per-elevator specification objects, allowing **heterogeneous fleets** (e.g., combining standard cars with faster or higher-capacity express cars).

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `elevator_id` | `int` | Unique, $\ge 0$ | Unique car identifier logged in allocation outputs and stop records. |
| `initial_floor` | `int` | `[min_floor, max_floor]` | Starting floor of the cabin at $t = 0$. |
| `max_speed_floors_per_sec` | `float` | $> 0\text{ floors/s}$ | Cruising speed $v_e$. |
| `start_time_sec` | `float` | $\ge 0\text{ s}$ | Acceleration overhead $t_{\text{start}, e}$ per travel leg. |
| `stop_time_sec` | `float` | $\ge 0\text{ s}$ | Deceleration overhead $t_{\text{stop}, e}$ per travel leg. |
| `door_open_time_sec` | `float` | $\ge 0\text{ s}$ | Door opening duration $t_{\text{open}, e}$ per stop. |
| `door_close_time_sec` | `float` | $\ge 0\text{ s}$ | Door closing duration $t_{\text{close}, e}$ per stop. |
| `dwell_time_sec` | `float` | $\ge 0\text{ s}$ | Base boarding/alighting dwell time $t_{\text{dwell}, e}$ per stop. |
| `max_capacity_passengers` | `int` | $> 0$ | Hard headcount limit $C_e$. |
| `max_weight_kg` | `float` | $> 0\text{ kg}$ | Hard load-sensor limit $W_{\max, e}$ triggering overload protection. |

### 2.4 `passengers`
Defines the ground-truth weight distribution used by the `RequestGenerator`.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `avg_weight_kg` | `float` | $> 0\text{ kg}$ | Mean passenger weight $\bar{w}$ used by the dispatcher's proactive weight projection. |
| `weight_std_kg` | `float` | $\ge 0\text{ kg}$ | Standard deviation for sampling hidden ground-truth passenger weights in the simulator. |
| `min_weight_kg` | `float` | $> 0\text{ kg}$ | Lower truncation bound for sampled passenger weights. |
| `max_weight_kg` | `float` | `> min_weight_kg` | Upper truncation bound for sampled passenger weights. |

### 2.5 `dispatcher`
Configures the allocation strategy and MC-DDI optimization hyperparameters.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `algorithm` | `string` | `"MC_DDI"`, `"SCAN"`, `"FCFS"` | Active local `IDispatcher` implementation (ignored if `esp32_bridge.enabled` is `true`). |
| `weight_buffer_ratio` | `float` | $[0.0, 0.3]$ | Proactive safety margin ratio ($W_{\text{buffer}} = \text{ratio} \times W_{\max, e}$) to absorb weight variance. |
| `soft_max_wait_sec` | `float` | $> 0\text{ s}$ | Wait-time threshold above which the quadratic starvation penalty activates. |
| `fairness_weight` | `float` | $\ge 0.0$ | Multiplier for the quadratic fairness penalty in MC-DDI. |
| `enable_idle_parking` | `bool` | `true` / `false` | Enables proactive repositioning of idle cars toward high-demand source floors. |
| `idle_parking_timeout_sec` | `float` | $> 0\text{ s}$ | Idle duration before an unoccupied car repositions. |

### 2.6 `sensors`
Configures real-time cabin monitoring thresholds, stochastic door delays, and deterministic fault injection.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `max_vibration_threshold` | `float` | $> 0$ | Safety cutoff above which an elevator is excluded from receiving new allocations. |
| `door_obstruction_probability` | `float` | $[0.0, 1.0]$ | Probability of a door obstruction occurring during any door-closing cycle. |
| `door_obstruction_delay_sec` | `float` | $\ge 0\text{ s}$ | Extra delay added when a door obstruction is detected. |
| `injected_faults[]` | `array` | Optional | Scheduled fault windows (`elevator_id`, `type`, `start_time_sec`, `end_time_sec`, `severity`) for testing safe failover. |

### 2.7 `traffic_generator`
Consumed by the Request Auto-Generator (ToDo #7) to synthesize time-based `(time, src, target)` streams.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `total_requests` | `int` | $> 0$ | Optional target cap on generated requests (or `null` to generate purely by `arrival_rate_per_min` across phases). |
| `distribution` | `string` | `"POISSON"`, `"UNIFORM"` | Inter-arrival time statistical model. |
| `phases[]` | `array` | Non-empty | Sequential time windows defining shifting traffic patterns (`name`, `start_time_sec`, `end_time_sec`, `arrival_rate_per_min`, and traffic ratios). |

**Phase Ratio Invariant:** For every phase object, the following must hold:
$$\text{up\_peak\_ratio} + \text{down\_peak\_ratio} + \text{inter\_floor\_ratio} = 1.0$$
* `up_peak_ratio`: Source is `lobby_floor`, target is a random non-lobby floor.
* `down_peak_ratio`: Source is a random non-lobby floor, target is `lobby_floor`.
* `inter_floor_ratio`: Source and target are two distinct random floors ($s \neq d$).

### 2.8 `esp32_bridge`
Controls Hardware-in-the-Loop (HIL) offloading of allocation decisions to the ESP32 microcontroller.

| Field | Type | Valid Range / Units | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `bool` | `true` / `false` | When `true`, routes all `IDispatcher.allocate()` calls to `ESP32DispatcherProxy`. |
| `transport` | `string` | `"WIFI_TCP"`, `"BLE_GATT"` | Physical wireless transport layer. |
| `host` | `string` | IPv4 / Hostname | ESP32 IP address (used when `transport == "WIFI_TCP"`). |
| `port` | `int` | `1024`–`65535` | TCP port listening on the ESP32. |
| `ble_service_uuid` | `string` | Valid UUID | GATT service UUID (used when `transport == "BLE_GATT"`). |
| `timeout_ms` | `int` | $> 0\text{ ms}$ | Network round-trip timeout before triggering a warning or fallback. |