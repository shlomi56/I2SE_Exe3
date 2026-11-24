# Simulator Output Format Specification
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #10)  
**Primary Output:** `simulation_output.csv`  
**Companion Metrics:** `simulation_summary.json`  

---

## 1. Design Requirement
Per the assignment specification, the simulator's primary output must follow **the same format as the time-based input request list (`time, src, target`), augmented with the assigned elevator allocation, plus a dedicated line for every physical elevator stop (with elevator ID)**.

---

## 2. Primary Output File (`simulation_output.csv`)

Stored as a UTF-8, comma-separated CSV file sorted chronologically by `time`. The first three columns preserve the exact input request format (`time,src,target`), extended with `elevator_id` and `event_type`.

### 2.1 Schema Definition

| Column | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| **`time`** | `float` | **Yes** | Event timestamp in seconds (rounded to $2$ decimal places), sorted in monotonically non-decreasing order. |
| **`src`** | `int` | **Yes** | For `ALLOC` / `REALLOC`: the passenger's origin floor. For `STOP`: the floor where the elevator physically stopped. |
| **`target`** | `int` / empty | Conditional | For `ALLOC` / `REALLOC`: the passenger's destination floor. For `STOP`: left empty (`""`). |
| **`elevator_id`** | `int` | **Yes** | The ID of the elevator assigned to the request (`ALLOC` / `REALLOC`) or executing the physical stop (`STOP`). |
| **`event_type`** | `string` | **Yes** | Event discriminator: `ALLOC` (initial call assignment), `REALLOC` (runtime overload re-dispatch), or `STOP` (physical elevator arrival/stop). |

### 2.2 Example Output (`simulation_output.csv`)
```csv
time,src,target,elevator_id,event_type
4.12,0,11,0,ALLOC
4.12,0,,0,STOP
9.85,0,4,1,ALLOC
9.85,0,,1,STOP
11.12,11,,0,STOP
15.30,0,11,0,ALLOC
16.85,4,,1,STOP
24.10,0,,0,STOP
38.10,11,,0,STOP
```

### 2.3 Downstream Parsing Properties
* Filtering rows where `event_type == "ALLOC"` reconstructs the exact input request stream (`time,src,target`) paired with its assigned `elevator_id`.
* Filtering rows where `event_type == "STOP"` yields the complete chronological stop trace for every elevator in the fleet.
* Rows where `event_type == "REALLOC"` explicitly audit runtime weight-overload recovery events.

---

## 3. Companion Metrics Summary (`simulation_summary.json`)

Alongside `simulation_output.csv`, `SimulationLogger` exports a structured JSON summary for automated regression testing (ToDo #9) and the final evaluation report (ToDo #12).

### 3.1 Example (`simulation_summary.json`)
```json
{
  "simulation_name": "Office_Building_Morning_Rush",
  "algorithm": "MC_DDI",
  "execution_mode": "LOCAL",
  "total_requests": 200,
  "completed_requests": 200,
  "total_journey_time_sec": 6842.50,
  "avg_journey_time_sec": 34.21,
  "avg_hall_wait_time_sec": 14.85,
  "max_hall_wait_time_sec": 52.40,
  "total_elevator_stops": 318,
  "overload_events": 3,
  "avg_dispatch_latency_ms": 0.42
}
```

### 3.2 Metric Definitions
* **`total_journey_time_sec`:** $\sum_{i=1}^{N} (T_{\text{arr}}(r_i) - t_i)$ — the primary global optimization objective from ToDo #2.
* **`avg_journey_time_sec`:** $\frac{1}{N} \sum_{i=1}^{N} (T_{\text{arr}}(r_i) - t_i)$.
* **`avg_hall_wait_time_sec`:** $\frac{1}{N} \sum_{i=1}^{N} (T_{\text{board}}(r_i) - t_i)$.
* **`max_hall_wait_time_sec`:** $\max_{i} (T_{\text{board}}(r_i) - t_i)$ — starvation verification metric.
* **`total_elevator_stops`:** Count of all `STOP` rows in `simulation_output.csv` — measures Destination Dispatch grouping efficiency.
* **`overload_events`:** Total number of runtime weight-sensor overload bumps (`REALLOC` rows).
* **`avg_dispatch_latency_ms`:** Mean round-trip decision latency of `IDispatcher.allocate()` (especially relevant in `ESP32` HIL mode).