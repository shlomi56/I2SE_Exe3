# Time-Based Request Format & Auto-Generator Specification
**Assignment:** Ex3 – End-to-End Design of a Smart Elevator System (ToDo #7)  
**Input Config:** `simulation_config.json`  
**Output Artifact:** `requests.csv`  

---

## 1. Time-Based Request List Format (`requests.csv`)

The request list is stored as a UTF-8, comma-separated CSV file with a mandatory header row.

### 1.1 Schema Definition

| Column | Type | Required | Constraints / Description |
| :--- | :--- | :--- | :--- |
| **`time`** | `float` | **Yes** | Request arrival timestamp in seconds ($\ge 0.0$, rounded to 2 decimal places). Rows must be sorted in monotonically non-decreasing order ($\text{time}_i \le \text{time}_{i+1}$). |
| **`src`** | `int` | **Yes** | Origin floor where the passenger calls the elevator ($\text{min\_floor} \le \text{src} \le \text{max\_floor}$). |
| **`target`** | `int` | **Yes** | Destination floor selected at the Destination Dispatch kiosk ($\text{min\_floor} \le \text{target} \le \text{max\_floor}$, strictly $\text{src} \neq \text{target}$). |
| **`weight_kg`** | `float` | Optional | Hidden ground-truth passenger weight in kg (rounded to 1 decimal place), used *only* by the simulator's physics engine to update cabin load sensors. |

> **Compatibility Rule (Class Interoperability):** The simulator's loader **must** accept both the strict 3-column format (`time,src,target`) and the 4-column enriched format (`time,src,target,weight_kg`). If `weight_kg` is omitted (e.g., when running a benchmark CSV from another student/group), the simulator automatically falls back to `passengers.avg_weight_kg` from `simulation_config.json`.

### 1.2 Example File (`requests.csv`)
```csv
time,src,target,weight_kg
4.12,0,11,72.4
9.85,0,4,88.1
15.30,0,11,64.0
28.44,7,0,79.5
41.09,3,12,102.3
```

---

## 2. Request Auto-Generator Specification

The auto-generator (`RequestGenerator`) takes `simulation_config.json` as input and deterministically outputs `requests.csv`.

### 2.1 Input Validation (Fail-Fast Preconditions)
Before generating any requests, the generator must validate the JSON:
1. $\text{min\_floor} < \text{max\_floor}$ and $\text{min\_floor} \le \text{lobby\_floor} \le \text{max\_floor}$.
2. Every phase in `traffic_generator.phases` satisfies:
   * $0.0 \le \text{start\_time\_sec} < \text{end\_time\_sec} \le \text{duration\_sec}$
   * $\text{arrival\_rate\_per\_min} > 0$
   * $|\text{up\_peak\_ratio} + \text{down\_peak\_ratio} + \text{inter\_floor\_ratio} - 1.0| < 10^{-6}$
3. Phases do not overlap in time.
4. $\text{min\_weight\_kg} \le \text{avg\_weight\_kg} \le \text{max\_weight\_kg}$.

### 2.2 Deterministic Seeding
Initialize a dedicated local pseudo-random number generator (e.g., `random.Random(seed)` or `numpy.random.default_rng(seed)`) using `simulation.random_seed` so identical configs always produce byte-for-byte identical CSV files.

### 2.3 Timestamp Generation (Piecewise Poisson Arrival Process)
Let $\lambda_p = \frac{\text{arrival\_rate\_per\_min}_p}{60.0}$ be the arrival rate (calls/sec) for phase $p$ over interval $[a_p, b_p]$.

* **Mode 1 — Exact `total_requests` Target (Default when `total_requests > 0`):**
  1. Compute each phase's expected call volume: $E_p = \lambda_p \cdot (b_p - a_p)$.
  2. Allocate integer counts $N_p$ to each phase proportionally to $E_p / \sum_q E_q$ (using the largest-remainder method so $\sum_p N_p = \text{total\_requests}$).
  3. By the conditional property of a Poisson process, given $N_p$ arrivals in $[a_p, b_p]$, draw $N_p$ timestamps i.i.d. from $\text{Uniform}(a_p, b_p)$ and sort them ascending. *(Guarantees both statistically valid Poisson arrivals and an exact request count for fair benchmarking.)*
* **Mode 2 — Open-Ended Poisson (When `total_requests` is `null` or $0$):**
  For each phase $p$, start at $t = a_p$ and repeatedly advance $t \leftarrow t + \Delta t$ where $\Delta t \sim \text{Exponential}(\lambda_p)$ until $t > b_p$.

*(Note: If `distribution` is set to `"UNIFORM"` instead of `"POISSON"`, space the $N_p$ timestamps evenly across $[a_p, b_p]$.)*

### 2.4 Origin–Destination (`src`, `target`) Sampling
Let $\mathcal{F} = \{\text{min\_floor}, \dots, \text{max\_floor}\}$ and $\mathcal{F}_{\text{non-lobby}} = \mathcal{F} \setminus \{\text{lobby\_floor}\}$. For each generated timestamp in phase $p$, sample a traffic type from the categorical distribution $(\text{up\_peak\_ratio},\; \text{down\_peak\_ratio},\; \text{inter\_floor\_ratio})$:

| Traffic Type | `src` Floor | `target` Floor |
| :--- | :--- | :--- |
| **`UP_PEAK`** | `lobby_floor` | Uniformly sampled from $\mathcal{F}_{\text{non-lobby}}$ |
| **`DOWN_PEAK`** | Uniformly sampled from $\mathcal{F}_{\text{non-lobby}}$ | `lobby_floor` |
| **`INTER_FLOOR`** | Sampled without replacement from $\mathcal{F}$ (pair of 2 distinct floors, guaranteeing $\text{src} \neq \text{target}$) | Second floor of the sampled pair |

### 2.5 Ground-Truth Weight Sampling
For each request, sample $w \sim \mathcal{N}(\mu, \sigma^2)$ using `avg_weight_kg` ($\mu$) and `weight_std_kg` ($\sigma$), clamp to $[\text{min\_weight\_kg},\; \text{max\_weight\_kg}]$, and round to 1 decimal place.

---

## 3. CLI & Programmatic Interface Contract

To keep the module cleanly separated from the simulator engine:
* **Programmatic API:** `RequestGenerator.generate(config: BuildingConfig, seed: Optional[int] = None) -> List[Request]`
* **CLI Usage:**
  ```bash
  python -m src.request_generator --config simulation_config.json --output data/requests.csv [--seed 42] [--strict-3col]
  ```
  * `--strict-3col` omits `weight_kg` to produce a bare `time,src,target` file for external sharing.

---

## 4. Output Invariants (Acceptance Tests for Implementation)
Any generated file must satisfy:
1. Row count equals `total_requests` (in Mode 1).
2. Timestamps are monotonically non-decreasing and within $[0.0,\; \text{duration\_sec}]$.
3. Every row satisfies $\text{min\_floor} \le \text{src} \le \text{max\_floor}$, $\text{min\_floor} \le \text{target} \le \text{max\_floor}$, and $\text{src} \neq \text{target}$.
4. Running the generator twice with the same `simulation_config.json` produces byte-for-byte identical output.
5. Empirical phase ratios (`UP_PEAK`, `DOWN_PEAK`, `INTER_FLOOR`) match configured ratios within statistical tolerance ($\pm 3\sigma$ binomial bound).