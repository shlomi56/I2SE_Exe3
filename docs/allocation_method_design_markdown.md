# Allocation Method Design – Smart Elevator Allocation System
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #5)  
**Algorithm:** Marginal-Cost Destination Dispatch Insertion (MC-DDI)  

---

## 1. Algorithm Overview
The core online allocation algorithm is **Marginal-Cost Destination Dispatch Insertion (MC-DDI)**. It directly optimizes the objective defined in ToDo #2—minimizing total passenger journey time (hall waiting time plus in-cabin transit time)—while enforcing real-time sensor safety interlocks, proactive hybrid weight limits, and the strict CPU/SRAM constraints of an ESP32 microcontroller.

Upon receiving a new call $r_{\text{new}} = (t_{\text{now}}, s, d)$, MC-DDI evaluates every operational elevator $e \in \mathcal{E}$ across four stages:

1. **Sensor & Safety Filtering:** Excludes elevators flagged as out-of-service or experiencing critical vibration anomalies.
2. **Order-Preserving Candidate Insertion:** Preserves the relative order of elevator $e$'s existing stop queue $\mathcal{Q}_e = (q_0, \dots, q_{k-1})$ (maintaining all existing pickup-before-dropoff precedence constraints) and tests every valid insertion pair $(i, j)$ with $i \le j$ for pickup $s$ and drop-off $d$. If $s$ or $d$ already exists at the insertion position, it is **merged (deduplicated)** rather than introducing a redundant stop.
3. **Proactive Hybrid Capacity Verification:** Simulates passenger headcount and projected weight across the candidate route $\mathcal{Q}'$, initialized from the cabin's live load sensor reading $W_{\text{sensor}, e}$. Any route violating headcount capacity or the weight safety buffer at any stop is pruned.
4. **Marginal Cost Evaluation:** Computes exact kinematic arrival times along $\mathcal{Q}'$ and evaluates the marginal system cost increase $\Delta C(e, \mathcal{Q}')$: the new passenger's projected journey time plus the cumulative delay imposed on all existing active passengers on elevator $e$ (plus a soft fairness penalty if any passenger's wait exceeds a threshold).

The controller commits $r_{\text{new}}$ to the elevator and route $(e^*, \mathcal{Q}^*)$ yielding the minimum $\Delta C$.

---

## 2. Core Algorithm Pseudocode

### 2.1 Main Allocation Routine (`allocate`)
```text
FUNCTION allocate(r_new, fleet_snapshots, active_assignments, config):
    best_decision = NULL
    best_cost = +INFINITY

    FOR EACH snapshot IN fleet_snapshots:
        e_id = snapshot.elevator_id
        e_cfg = config.elevators[e_id]

        # 1. Sensor Safety Gate
        IF NOT snapshot.is_operational OR snapshot.vibration_level > config.max_vibration:
            CONTINUE

        current_queue = active_assignments[e_id].stop_queue       # Q_e = [q_0, ..., q_{k-1}]
        active_reqs   = active_assignments[e_id].requests         # onboard + assigned waiting

        # Compute physics anchor (point-of-no-return braking floor & arrival offset)
        (anchor_floor, anchor_time) = compute_commit_point(snapshot, e_cfg, r_new.timestamp)

        # Baseline arrival times for existing requests BEFORE inserting r_new
        base_times = simulate_route_times(anchor_floor, anchor_time, current_queue, snapshot, e_cfg)

        # 2. Generate all order-preserving insertions of (src, target)
        k = LENGTH(current_queue)
        FOR i FROM 0 TO k:
            # Cannot board immediately at current floor if car is flagged AT_CAPACITY
            IF i == 0 AND snapshot.is_at_capacity AND NOT has_alighting_at(active_reqs, r_new.src):
                CONTINUE

            # Insert src at index i (merge if adjacent floor equals src)
            (queue_with_src, src_idx) = insert_or_merge(current_queue, r_new.src, i)

            FOR j FROM (src_idx + 1) TO LENGTH(queue_with_src):
                # Insert target at index j (merge if adjacent floor equals target)
                (cand_queue, dst_idx) = insert_or_merge(queue_with_src, r_new.target, j)

                # 3. Proactive Hybrid Weight & Headcount Feasibility Check
                IF NOT is_route_capacity_feasible(
                        snapshot, e_cfg, active_reqs, r_new, cand_queue, src_idx, dst_idx,
                        config.avg_passenger_weight_kg, config.weight_buffer_ratio):
                    CONTINUE

                # 4. Kinematic Time Simulation & Marginal Cost Calculation
                new_times = simulate_route_times(anchor_floor, anchor_time, cand_queue, snapshot, e_cfg)

                # New passenger's total journey time (retains original timestamp for re-dispatches)
                new_req_journey = new_times[dst_idx] - r_new.timestamp

                # Added delay imposed on all existing active passengers on this car
                existing_delay = 0.0
                fairness_penalty = 0.0
                FOR EACH req IN active_reqs:
                    old_arr = get_arrival_time(req, current_queue, base_times)
                    new_arr = get_arrival_time(req, cand_queue, new_times)
                    existing_delay += (new_arr - old_arr)

                    # Soft starvation guard if wait exceeds threshold (e.g., 60s)
                    proj_wait = get_board_time(req, cand_queue, new_times) - req.timestamp
                    IF proj_wait > config.soft_max_wait_sec:
                        fairness_penalty += config.fairness_weight * (proj_wait - config.soft_max_wait_sec)^2

                total_marginal_cost = new_req_journey + existing_delay + fairness_penalty

                # 5. Best Selection with Deterministic Tie-Breaking
                IF is_better(total_marginal_cost, cand_queue, snapshot, best_cost, best_decision):
                    best_cost = total_marginal_cost
                    best_decision = AllocationDecision(
                        req_id = r_new.req_id,
                        elevator_id = e_id,
                        planned_route = cand_queue,
                        estimated_cost = total_marginal_cost
                    )

    # 6. Fallback if all cars failed strict buffer/capacity checks
    IF best_decision IS NULL:
        best_decision = fallback_tail_allocation(r_new, fleet_snapshots, active_assignments, config)

    RETURN best_decision
```

### 2.2 Kinematic Route Simulator (`simulate_route_times`)
```text
FUNCTION simulate_route_times(anchor_floor, anchor_time, route, snapshot, e_cfg):
    times = []
    curr_floor = anchor_floor
    t = anchor_time

    # Account for live door obstruction if car is currently held at a floor
    IF snapshot.door_obstructed:
        t += e_cfg.door_obstruction_penalty

    FOR EACH stop_floor IN route:
        IF stop_floor != curr_floor:
            leg_time = e_cfg.start_time + ABS(stop_floor - curr_floor) / e_cfg.max_speed + e_cfg.stop_time
            t += leg_time
            curr_floor = stop_floor

        times.APPEND(t)   # Arrival / alighting timestamp at this stop
        t += (e_cfg.door_open_time + e_cfg.dwell_time + e_cfg.door_close_time)

    RETURN times
```

---

## 3. Emergent Destination Dispatch Grouping
Because `insert_or_merge` deduplicates shared stops, MC-DDI naturally groups passengers traveling to identical or adjacent floors without rigid hardcoded zoning rules:
* **Exact Match Grouping:** If Elevator 1 is already scheduled to stop at Floor 1 and Floor 8, a new call `(1 -> 8)` merges into both existing stops. It adds $0$ extra acceleration/deceleration cycles and $0$ extra door cycles, yielding `existing_delay = 0.0` and outperforming an idle or differently routed car by $8\text{--}15\text{ s}$ per shared stop.
* **Adjacent Floor Grouping:** A call `(1 -> 7)` inserted into `[1, 8]` adds only a single short stop along the same travel direction, incurring minimal marginal detour compared to dispatching a separate car.

---

## 4. Edge-Case Handling

| Edge Case | MC-DDI Resolution Strategy |
| :--- | :--- |
| **Point-of-No-Return (Braking Distance)** | `compute_commit_point` determines the nearest floor at which a moving car can safely decelerate given its current position and velocity. Candidate routes begin at `anchor_floor`, preventing physically impossible instantaneous stops. |
| **All Cars Temporarily Full** | If `best_decision` remains `NULL` after the main loop, `fallback_tail_allocation` appends `[r_new.src, r_new.target]` to the end of the operational elevator whose current queue empties earliest (after all onboard passengers alight, guaranteeing free capacity). |
| **Runtime Weight Overload Re-Dispatch** | When a boarding passenger trips the load sensor ($W_{\text{sensor}, e} > W_{\max, e}$) and is bumped, the simulator flags `is_at_capacity = True` and re-invokes `allocate` using the passenger's **original `timestamp`**. The overloaded car is blocked from index-$0$ boarding, while the passenger's accumulated wait time elevates their priority via `fairness_penalty`. |
| **Deterministic Tie-Breaking** | When two candidates have identical $\Delta C$ (within $\epsilon = 10^{-3}\text{ s}$), `is_better` breaks ties by: (1) fewer newly added stops (favors DD grouping), (2) lower current cabin weight, and (3) lower `elevator_id`. |
| **Idle Car Parking (Traffic Pattern Extension)** | Optional traffic-aware enhancement: track a rolling window of recent source floors (e.g., last $5\text{ minutes}$). If an elevator stays `IDLE` for $> 30\text{ s}$, proactively reposition it toward the highest-demand source floor (such as the lobby during morning up-peak) without committing any passenger. |

---

## 5. Computational Complexity & ESP32 Feasibility
Let $m$ be the number of elevators and $k = |\mathcal{Q}_e|$ be the number of stops currently queued on an elevator.
* **Time Complexity:** For each elevator, there are $\frac{(k+1)(k+2)}{2} = O(k^2)$ valid insertion pairs $(i, j)$. Evaluating capacity and kinematic arrival times per pair takes $O(k)$ time, yielding **$O(m \cdot k^3)$** per incoming request.
* **ESP32 Benchmark Estimate:** In a typical $10\text{--}20$ story building with $m = 4$ elevators and $k \le 12$ active stops per car, the inner loop performs at most $\approx 4 \times 91 \times 12 \approx 4,368$ lightweight floating-point operations. On a $240\text{ MHz}$ ESP32 with hardware floating-point support, this executes in **under $1\text{ ms}$**—well below Wi-Fi latency and human reaction times.
* **Memory Footprint:** Using fixed-size stack arrays (`int cand_queue[MAX_STOPS]`, `float times[MAX_STOPS]` with `MAX_STOPS = 32`), the algorithm requires **$< 2\text{ KB}$ of SRAM** and performs **zero dynamic heap allocations**, preventing heap fragmentation during long runs.

---

## 6. Baseline Algorithms for Benchmarking
To empirically verify MC-DDI's performance gains against traditional elevator scheduling, the simulator implements two baseline `IDispatcher` strategies:
1. **First Come, First Served (`FCFSDispatcher`):** Selects the operational elevator that will finish its current stop queue earliest and appends `(src, target)` strictly to the end of its queue.
2. **One-Way Sweep (`SCANDispatcher`):** Inserts `src` and `target` along each elevator's current directional sweep (sweeping UP to the highest call, then reversing DOWN to the lowest call), selecting the car with the shortest estimated pickup time.