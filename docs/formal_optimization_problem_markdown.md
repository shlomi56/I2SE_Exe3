# Formal Definition of the Smart Elevator Optimization Problem
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #2)  
**Problem Class:** Online Multi-Vehicle Dial-a-Ride Problem (Online $m$-DARP) with Destination Dispatch  

---

## 1. Problem Overview
Given a multi-story building equipped with a fleet of smart elevators operating under a **Destination Dispatch (DD)** system, passenger requests arrive dynamically over time. Unlike traditional elevator systems where passengers only indicate direction ("Up" or "Down"), Destination Dispatch captures both the source and target floors at the time of the call. 

The objective is to design an **online (real-time)** allocation controller that assigns each incoming request to an elevator and updates its stop schedule to minimize the **total journey time** across all passengers, while strictly respecting physical kinematics, capacity/weight limits, and real-time sensor safety constraints.

---

## 2. System Model & Notation

### 2.1 Building & Elevator Fleet
* **Floors:** A finite ordered set of floors $\mathcal{F} = \{f_{\min}, f_{\min}+1, \dots, f_{\max}\}$, where $f_{\min}$ may represent basement levels.
* **Elevators:** A set of $m$ smart elevators $\mathcal{E} = \{1, 2, \dots, m\}$.
* **Physical Parameters (per elevator $e \in \mathcal{E}$):**
  * Cruising speed: $v_e$ (floors/sec)
  * Acceleration / start delay: $t_{\text{start}, e}$ (sec)
  * Deceleration / stop delay: $t_{\text{stop}, e}$ (sec)
  * Door opening & closing times: $t_{\text{open}, e}$, $t_{\text{close}, e}$ (sec)
  * Passenger boarding / alighting dwell time: $t_{\text{dwell}, e}$ (sec)
  * Maximum passenger headcount capacity: $C_e$
  * Maximum weight load capacity: $W_{\max, e}$ (kg)

### 2.2 Real-Time Elevator State Vector
At any continuous timestamp $t \ge 0$, each elevator $e \in \mathcal{E}$ is described by a state vector populated by its onboard sensors and controller:
$$S_e(t) = \big(p_e(t),\; \delta_e(t),\; \mathcal{Q}_e(t),\; \mathcal{A}_e(t),\; W_{\text{sensor}, e}(t),\; \sigma_e(t)\big)$$

Where:
* $p_e(t) \in [f_{\min}, f_{\max}]$ is the current vertical position.
* $\delta_e(t) \in \{\text{UP}, \text{DOWN}, \text{IDLE}\}$ is the current travel direction.
* $\mathcal{Q}_e(t) = (q_1, q_2, \dots, q_k)$ is the ordered sequence of scheduled future stops.
* $\mathcal{A}_e(t)$ is the set of active requests assigned to elevator $e$ (both passengers currently onboard and those waiting at their source floors).
* $W_{\text{sensor}, e}(t) \ge 0$ is the live cabin weight measured by the onboard load sensor.
* $\sigma_e(t)$ is the operational health and safety status (e.g., `NORMAL`, `DOOR_OBSTRUCTED`, `OVERLOAD`, `VIBRATION_FAULT`, `OUT_OF_SERVICE`).

### 2.3 Online Request Stream
Passenger calls arrive chronologically as a stream $R = (r_1, r_2, \dots, r_n)$:
$$r_i = (t_i,\; s_i,\; d_i)$$
where $t_i \in \mathbb{R}_{\ge 0}$ is the call timestamp, $s_i \in \mathcal{F}$ is the source floor, and $d_i \in \mathcal{F}$ is the target floor ($s_i \neq d_i$). In the physical simulation, each passenger also carries a ground-truth weight $w_i$ that is hidden from the dispatcher until boarding, when it is registered by $W_{\text{sensor}, e}(t)$.

---

## 3. Decision Variables & Operational Properties

Upon the arrival of request $r_i$ at time $t_i$, the dispatcher must immediately determine:
1. **Elevator Assignment:** $a_i \in \mathcal{E}$, the elevator car selected to serve $r_i$.
2. **Route Update:** An updated ordered stop sequence $\mathcal{Q}_{a_i}'$ that inserts pickup $s_i$ and drop-off $d_i$ into $\mathcal{Q}_{a_i}(t_i)$.

**Operational Properties:**
* **Online (Non-Anticipative):** The decision $a_i$ depends solely on the current fleet state $\{S_e(t_i)\}_{e \in \mathcal{E}}$ and past requests $\{r_1, \dots, r_i\}$. Future requests $\{r_j : j > i\}$ are unknown.
* **Irrevocable Commitment:** Because Destination Dispatch immediately displays the assigned elevator ID to the passenger at the hall kiosk, $a_i$ cannot be reassigned unless a runtime exception (such as a weight overload or mechanical fault) occurs.

---

## 4. Objective Function

For each request $r_i$, let $T_{\text{board}}(r_i)$ be the boarding time at $s_i$ and $T_{\text{arr}}(r_i)$ be the arrival time at destination $d_i$. The **Total Journey Time** (referred to as total waiting time in the problem statement) is:
$$J(r_i) = T_{\text{arr}}(r_i) - t_i = \underbrace{\big(T_{\text{board}}(r_i) - t_i\big)}_{\text{Hall Waiting Time } W_i} + \underbrace{\big(T_{\text{arr}}(r_i) - T_{\text{board}}(r_i)\big)}_{\text{In-Cabin Transit Time } \tau_i}$$

### 4.1 Global Evaluation Objective
Across the entire request stream $R$, minimize the cumulative (and equivalently, average) journey time:
$$\min_{a_1, \dots, a_n} \;\; \sum_{i=1}^{n} J(r_i) = \min_{a_1, \dots, a_n} \;\; \sum_{i=1}^{n} \big(T_{\text{arr}}(r_i) - t_i\big)$$

### 4.2 Online Marginal Cost Objective
Because future arrivals are unknown, the online dispatcher evaluates each feasible elevator $e \in \mathcal{E}_{\text{feasible}}(r_i)$ by computing the exact **marginal increase in total system journey time**. Let $\mathcal{Q}_e^*(r_i)$ be the best feasible route for elevator $e$ after inserting $(s_i, d_i)$:
$$\Delta C(e, r_i) = \underbrace{\big(T_{\text{arr}}(r_i \mid \mathcal{Q}_e^*(r_i)) - t_i\big)}_{\text{New passenger's journey time}} \;+\; \underbrace{\sum_{r_j \in \mathcal{A}_e(t_i)} \Big( T_{\text{arr}}^{\text{new}}(r_j \mid \mathcal{Q}_e^*(r_i)) - T_{\text{arr}}^{\text{old}}(r_j \mid \mathcal{Q}_e(t_i)) \Big)}_{\text{Added delay imposed on existing passengers of elevator } e}$$

At time $t_i$, the controller selects:
$$a_i = \arg\min_{e \in \mathcal{E}_{\text{feasible}}(r_i)} \Delta C(e, r_i)$$

*Destination Grouping Property:* If elevator $e$ is already scheduled to stop at $s_i$ and $d_i$ in the proper order, inserting $r_i$ introduces zero additional stop overhead and zero delay to existing passengers, naturally clustering passengers with shared or adjacent destinations into the same car.

---

## 5. Constraints

An assignment $a_i = e$ with updated stop sequence $\mathcal{Q}_e'$ is feasible if and only if all of the following hold:

1. **Precedence Constraint:** Pickup must strictly precede drop-off:
   $$\text{index}_{\mathcal{Q}_e'}(s_i) < \text{index}_{\mathcal{Q}_e'}(d_i)$$

2. **Dynamic Capacity & Proactive Weight Constraint:** Because exact weights of unboarded passengers are unknown at call time, the controller enforces both headcount and a **hybrid weight projection** at every future stop $k \in \{1, \dots, |\mathcal{Q}_e'|\}$:
   $$\hat{L}_e(k) \le C_e$$
   $$\hat{W}_e(k) = W_{\text{sensor}, e}(t_i) + \sum_{j \in \text{boardings} \le k} \bar{w}_j - \sum_{j \in \text{alightings} \le k} \bar{w}_j \;\le\; W_{\max, e} - W_{\text{buffer}}$$
   where $W_{\text{sensor}, e}(t_i)$ is the live cabin weight, $\bar{w}_j$ is the estimated mean passenger weight (e.g., $75\text{ kg}$), and $W_{\text{buffer}}$ is a configurable safety margin (e.g., $10\%$ of $W_{\max, e}$).

3. **Physical Kinematics & Timing:** The transit duration between consecutive distinct stops $q_k$ and $q_{k+1}$ obeys:
   $$\Delta t_{\text{move}}(q_k, q_{k+1}) = t_{\text{start}, e} + \frac{|q_{k+1} - q_k|}{v_e} + t_{\text{stop}, e}$$
   Each stop incurs door and passenger transfer overhead: $t_{\text{open}, e} + t_{\text{dwell}, e} + t_{\text{close}, e}$ (plus any door obstruction delay reported by sensors).

4. **Sensor & Safety Interlocks:** Elevator $e$ is excluded from $\mathcal{E}_{\text{feasible}}(r_i)$ if $\sigma_e(t_i)$ indicates `OUT_OF_SERVICE`, `VIBRATION_FAULT`, or `OVERLOAD`.

5. **Starvation Prevention (Optional Fairness Constraint):** To prevent isolated requests at extreme floors from being indefinitely deferred during rush hour, an optional hard wait cap $W_i \le W_{\max}$ or quadratic waiting-time penalty $\Delta(W_j^2)$ may be applied inside $\Delta C(e, r_i)$.

---

## 6. Runtime Overload Recovery (Reactive Handling)
Because real passenger weights $w_i$ can exceed $\bar{w}_i$ (or passengers may tailgate on a single call), a cabin may still exceed $W_{\max, e}$ upon physical boarding. The system handles this via a closed-loop sensor recovery protocol:
1. **Safety Interlock:** The load sensor detects $W_{\text{sensor}, e}(t) > W_{\max, e}$, sets $\sigma_e(t) = \text{OVERLOAD}$, holds the doors open, and inhibits departure.
2. **Capacity Lock:** Once excess passenger(s) step back out, the elevator marks itself `AT_CAPACITY`, blocking any new pickup allocations until a subsequent drop-off lowers $W_{\text{sensor}, e}(t)$.
3. **Priority Re-Dispatch:** The bumped request $r_i$ is immediately re-injected into the online allocator at $t_{\text{now}}$ while retaining its original call timestamp $t_i$ so its accumulated wait time is accurately penalized.

---

## 7. Computational Complexity & Edge Cases
* **Complexity:** Even the offline version (all requests known upfront) is **NP-hard** (reducible to the Capacitated Dial-a-Ride Problem). In the online setting, marginal-cost insertion combined with directional ordering (SCAN/LOOK) provides a low-latency heuristic well suited for real-time execution on an **ESP32 microcontroller**.
* **Point-of-No-Return (Braking Distance):** If elevator $e$ is already passing or within its physical braking distance of floor $s_i$, it cannot stop immediately at $s_i$ and must service it on a subsequent pass.
* **Simultaneous Calls:** Calls arriving at identical timestamps $t_i$ are processed deterministically (e.g., ordered by request ID) or evaluated in micro-batches.
* **All Cars Temporarily Full:** If $\mathcal{E}_{\text{feasible}}(r_i) = \emptyset$ at $t_i$, the allocator schedules $s_i$ after the earliest planned drop-off that frees sufficient capacity, or holds $r_i$ in a pending retry queue.