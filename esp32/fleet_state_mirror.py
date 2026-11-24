# FleetStateMirror - fixed-size digital twin of the fleet (LLD section 5).
#
# MicroPython-compatible (no dataclasses/typing/enum). Every buffer is allocated once in __init__;
# load_config / update_telemetry only overwrite slots, so SRAM use is constant during a run and a
# payload larger than the compile-time limits is rejected instead of growing the heap.

MAX_ELEVATORS = 8
MAX_STOPS = 32
MAX_REQS = 32


class CapacityExceeded(ValueError):
    pass


class FleetStateMirror:
    def __init__(self, max_elevators=MAX_ELEVATORS, max_stops=MAX_STOPS, max_reqs=MAX_REQS):
        self.max_elevators = max_elevators
        self.max_stops = max_stops
        self.max_reqs = max_reqs
        self.initialized = False
        self.n = 0
        # building / MC-DDI parameters
        self.min_floor = 0
        self.max_floor = 0
        self.avg_weight = 75.0
        self.buffer_ratio = 0.0
        self.soft_max_wait = 60.0
        self.fairness = 0.0
        self.obstruction_delay = 0.0
        self.max_vibration = 0.0
        m = max_elevators
        # static per-car config
        self.eid = [0] * m
        self.speed = [0.0] * m
        self.start = [0.0] * m
        self.stop = [0.0] * m
        self.door_cycle = [0.0] * m
        self.max_w = [0.0] * m
        self.cap = [0] * m
        # live telemetry
        self.t = [0.0] * m
        self.pos = [0.0] * m
        self.spd = [0.0] * m
        self.w = [0.0] * m
        self.vib = [0.0] * m
        self.obs = [0] * m
        self.op = [0] * m
        self.atcap = [0] * m
        # stop queues
        self.queue = [[0] * max_stops for _ in range(m)]
        self.qn = [0] * m
        # active requests (onboard + assigned waiting), in the order the host sent them
        self.r_id = [[0] * max_reqs for _ in range(m)]
        self.r_ts = [[0.0] * max_reqs for _ in range(m)]
        self.r_src = [[0] * max_reqs for _ in range(m)]
        self.r_dst = [[0] * max_reqs for _ in range(m)]
        self.r_on = [[0] * max_reqs for _ in range(m)]
        self.rn = [0] * m

    # ------------------------------------------------------------------ LLD methods

    def load_config(self, cfg):
        elevators = cfg["elevators"]
        if len(elevators) > self.max_elevators:
            raise CapacityExceeded("too many elevators: %d > %d" % (len(elevators), self.max_elevators))
        self.min_floor = int(cfg["min_floor"])
        self.max_floor = int(cfg["max_floor"])
        self.avg_weight = float(cfg["avg_weight_kg"])
        self.buffer_ratio = float(cfg["weight_buffer_ratio"])
        self.soft_max_wait = float(cfg["soft_max_wait_sec"])
        self.fairness = float(cfg["fairness_weight"])
        self.obstruction_delay = float(cfg["door_obstruction_delay_sec"])
        self.max_vibration = float(cfg["max_vibration"])
        ordered = sorted(elevators, key=lambda e: e["id"])
        for c, e in enumerate(ordered):
            self.eid[c] = int(e["id"])
            self.speed[c] = float(e["speed"])
            self.start[c] = float(e["start"])
            self.stop[c] = float(e["stop"])
            # same summation order as ElevatorConfig.door_cycle_time (open + dwell + close)
            self.door_cycle[c] = float(e["open"]) + float(e["dwell"]) + float(e["close"])
            self.max_w[c] = float(e["max_w"])
            self.cap[c] = int(e["cap"])
            self.qn[c] = 0
            self.rn[c] = 0
        self.n = len(ordered)
        self.initialized = True

    def slot_of(self, elevator_id):
        for c in range(self.n):
            if self.eid[c] == elevator_id:
                return c
        return -1

    def update_telemetry(self, fleet_state):
        """Overwrite telemetry, stop queues and active requests from an ALLOCATE_REQ fleet_state."""
        for e in fleet_state:
            c = self.slot_of(int(e["id"]))
            if c < 0:
                raise ValueError("unknown elevator id %s" % e["id"])
            q = e["queue"]
            reqs = e["reqs"]
            if len(q) > self.max_stops:
                raise CapacityExceeded("E%d queue %d > MAX_STOPS %d" % (self.eid[c], len(q), self.max_stops))
            if len(reqs) > self.max_reqs:
                raise CapacityExceeded("E%d has %d requests > MAX_REQS %d" % (self.eid[c], len(reqs), self.max_reqs))
            self.t[c] = float(e["t"])
            self.pos[c] = float(e["pos"])
            self.spd[c] = float(e["spd"])
            self.w[c] = float(e["w"])
            self.vib[c] = float(e["vib"])
            self.obs[c] = 1 if e["obs"] else 0
            self.op[c] = 1 if e["op"] else 0
            self.atcap[c] = 1 if e["cap"] else 0
            qc = self.queue[c]
            for k in range(len(q)):
                qc[k] = int(q[k])
            self.qn[c] = len(q)
            for k in range(len(reqs)):
                rid, ts, src, dst, on = reqs[k]
                self.r_id[c][k] = int(rid)
                self.r_ts[c][k] = float(ts)
                self.r_src[c][k] = int(src)
                self.r_dst[c][k] = int(dst)
                self.r_on[c][k] = 1 if on else 0
            self.rn[c] = len(reqs)

    def commit_assignment(self, decision, req):
        """Apply a decision locally (keeps the twin consistent between host syncs)."""
        c = self.slot_of(decision["elevator_id"])
        route = decision["route"]
        if c < 0 or len(route) > self.max_stops or self.rn[c] >= self.max_reqs:
            return False
        for k in range(len(route)):
            self.queue[c][k] = route[k]
        self.qn[c] = len(route)
        k = self.rn[c]
        self.r_id[c][k] = req[0]
        self.r_ts[c][k] = req[1]
        self.r_src[c][k] = req[2]
        self.r_dst[c][k] = req[3]
        self.r_on[c][k] = 0
        self.rn[c] = k + 1
        return True
