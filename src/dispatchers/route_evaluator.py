"""``RouteEvaluator`` — shared kinematic timing, proactive capacity check and marginal cost (LLD §3.1).

All dispatchers (FCFS, SCAN, MC-DDI and, in Phase 6, the ESP32 mirror) use this class, so they share
identical physics math. Its time model mirrors ``src.simulation.elevator``:

* a stationary car starts every leg with ``start_time`` and ends it with ``stop_time``;
* a car already moving (signed ``snapshot.speed != 0``) needs no ``start_time`` for a stop ahead of its
  braking floor; a stop behind it costs a door-less turnaround at the braking floor;
* every stop adds ``door_open + dwell + door_close``; a live door obstruction adds its delay up front.

Projected weights never use a passenger's hidden ``actual_weight_kg``: the hybrid projection starts from
the load-sensor reading and adds/subtracts ``avg_weight`` per boarding/alighting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from src.models.dto import BuildingConfig, ElevatorConfig, Request, SensorSnapshot
from src.models.route_semantics import BOARDING_NOW, RoutePlan, braking_floor, plan_route, split_active

INFEASIBLE = float("inf")


def insert_or_merge(queue: Sequence[int], floor: int, index: int, min_merge_index: int = 0) -> Tuple[List[int], int]:
    """Insert ``floor`` at ``index`` unless an adjacent stop already is that floor, in which case the new
    stop is merged into it. Returns ``(new_queue, position_of_floor)``. ``min_merge_index`` forbids merging
    into stops before that position (used to keep a drop-off strictly after its pickup)."""
    q = list(queue)
    if index - 1 >= min_merge_index and index - 1 < len(q) and q[index - 1] == floor:
        return q, index - 1
    if index < len(q) and q[index] == floor:
        return q, index
    q.insert(index, floor)
    return q, index


@dataclass
class CarContext:
    """Per-elevator data cached once per allocation (base route timing)."""

    snapshot: SensorSnapshot
    config: ElevatorConfig
    onboard: List[Request]
    waiting: List[Request]
    base_plan: RoutePlan
    base_times: List[float]
    t0: float


@dataclass
class CandidateEvaluation:
    plan: RoutePlan
    times: List[float]
    cost: float
    new_board_time: float
    new_arrival_time: float


class RouteEvaluator:
    def __init__(
        self,
        avg_weight_kg: float = 75.0,
        weight_buffer_ratio: float = 0.0,
        soft_max_wait_sec: float = 60.0,
        fairness_weight: float = 0.0,
        door_obstruction_delay_sec: float = 0.0,
    ) -> None:
        self.avg_weight_kg = avg_weight_kg
        self.weight_buffer_ratio = weight_buffer_ratio
        self.soft_max_wait_sec = soft_max_wait_sec
        self.fairness_weight = fairness_weight
        self.door_obstruction_delay_sec = door_obstruction_delay_sec

    @classmethod
    def from_config(cls, config: BuildingConfig) -> "RouteEvaluator":
        return cls(
            avg_weight_kg=config.avg_passenger_weight_kg,
            weight_buffer_ratio=config.weight_buffer_ratio,
            soft_max_wait_sec=config.dispatcher.soft_max_wait_sec,
            fairness_weight=config.dispatcher.fairness_weight,
            door_obstruction_delay_sec=config.sensors.door_obstruction_delay_sec,
        )

    # ================================================================== LLD public methods

    @staticmethod
    def estimate_leg_time(config: ElevatorConfig, start_floor: float, end_floor: int) -> float:
        """``t_start + |d| / v + t_stop`` for a leg between distinct floors (0 when already there)."""
        distance = abs(end_floor - start_floor)
        if distance < 1e-9:
            return 0.0
        return config.start_time + distance / config.max_speed + config.stop_time

    def check_weight_feasibility(
        self,
        snapshot: SensorSnapshot,
        config: ElevatorConfig,
        active_reqs: List[Request],
        candidate_req: Request,
        route: List[int],
        avg_weight: Optional[float] = None,
        buffer_ratio: Optional[float] = None,
    ) -> bool:
        """Proactive hybrid capacity check of ``route`` with ``candidate_req`` added (headcount + weight)."""
        onboard, waiting = split_active(active_reqs)
        plan = plan_route(route, onboard, waiting + [candidate_req])
        return self._capacity_ok(snapshot, config, onboard, waiting, candidate_req, plan, avg_weight, buffer_ratio)

    def compute_marginal_cost(
        self,
        snapshot: SensorSnapshot,
        config: ElevatorConfig,
        active_reqs: List[Request],
        candidate_req: Request,
        candidate_route: List[int],
        current_time: float,
        base_route: Optional[List[int]] = None,
    ) -> float:
        """ΔC(e, r): new passenger's journey time + delay added to the car's existing passengers
        + fairness penalty. ``base_route`` is the car's current queue (derived from the candidate route
        by removing ``candidate_req`` if omitted)."""
        onboard, waiting = split_active(active_reqs)
        if base_route is None:
            base_route = plan_route(candidate_route, onboard, waiting).route
        ctx = self.make_context(snapshot, config, active_reqs, base_route, current_time)
        plan = plan_route(candidate_route, onboard, waiting + [candidate_req])
        return self._cost(ctx, candidate_req, plan).cost

    # ================================================================== building blocks

    def make_context(
        self,
        snapshot: SensorSnapshot,
        config: ElevatorConfig,
        active_reqs: Sequence[Request],
        stop_queue: Sequence[int],
        current_time: float,
    ) -> CarContext:
        onboard, waiting = split_active(active_reqs)
        base_plan = plan_route(stop_queue, onboard, waiting)
        t0 = current_time
        return CarContext(
            snapshot, config, onboard, waiting, base_plan, self.simulate_route_times(snapshot, config, base_plan.route, t0), t0
        )

    def simulate_route_times(
        self, snapshot: SensorSnapshot, config: ElevatorConfig, route: Sequence[int], t0: Optional[float] = None
    ) -> List[float]:
        """Arrival timestamp at each stop of ``route`` (``times[k]`` = STOP time of ``route[k]``)."""
        t = snapshot.timestamp if t0 is None else t0
        if snapshot.door_obstructed:
            t += self.door_obstruction_delay_sec
        pos = snapshot.position
        moving = snapshot.speed
        bf = braking_floor(snapshot.position, snapshot.speed)
        v = config.max_speed
        times: List[float] = []
        for stop in route:
            if moving != 0.0:
                ahead = (moving > 0 and stop >= bf) or (moving < 0 and stop <= bf)
                if ahead:
                    t += abs(stop - pos) / v + config.stop_time
                else:  # door-less turnaround at the braking floor, then a fresh leg back
                    t += abs(bf - pos) / v + config.stop_time
                    t += self.estimate_leg_time(config, bf, stop)
                moving = 0.0
            else:
                t += self.estimate_leg_time(config, pos, stop)
            times.append(t)
            t += config.door_cycle_time
            pos = float(stop)
        return times

    def queue_completion_time(self, ctx: CarContext) -> float:
        """When the car finishes its current queue (doors closed at the last stop)."""
        if not ctx.base_times:
            if ctx.snapshot.speed != 0.0:  # moving with nothing queued: it will stop at its braking floor
                bf = braking_floor(ctx.snapshot.position, ctx.snapshot.speed)
                return ctx.t0 + abs(bf - ctx.snapshot.position) / ctx.config.max_speed + ctx.config.stop_time
            return ctx.t0
        return ctx.base_times[-1] + ctx.config.door_cycle_time

    def evaluate_candidate(self, ctx: CarContext, new_req: Request, candidate_route: Sequence[int]) -> Optional[CandidateEvaluation]:
        """Resolve, capacity-check and cost a candidate route. ``None`` if infeasible."""
        plan = plan_route(candidate_route, ctx.onboard, ctx.waiting + [new_req])
        if not self._capacity_ok(ctx.snapshot, ctx.config, ctx.onboard, ctx.waiting, new_req, plan, None, None):
            return None
        return self._cost(ctx, new_req, plan)

    def evaluate_unchecked(self, ctx: CarContext, new_req: Request, candidate_route: Sequence[int]) -> CandidateEvaluation:
        """Cost a candidate route without the capacity check (used by baselines and the fallback)."""
        return self._cost(ctx, new_req, plan_route(candidate_route, ctx.onboard, ctx.waiting + [new_req]))

    # ================================================================== internals

    def _capacity_ok(
        self,
        snapshot: SensorSnapshot,
        config: ElevatorConfig,
        onboard: Sequence[Request],
        waiting: Sequence[Request],
        new_req: Request,
        plan: RoutePlan,
        avg_weight: Optional[float],
        buffer_ratio: Optional[float],
    ) -> bool:
        avg = self.avg_weight_kg if avg_weight is None else avg_weight
        ratio = self.weight_buffer_ratio if buffer_ratio is None else buffer_ratio
        w_limit = config.max_weight_kg * (1.0 - ratio)

        n = len(plan.route)
        boards = [0] * n
        alights = [0] * n
        now_boarders = 0
        for r in list(waiting) + [new_req]:
            b = plan.board_idx[r.req_id]
            if b == BOARDING_NOW:
                now_boarders += 1
            else:
                boards[b] += 1
        for r in list(onboard) + list(waiting) + [new_req]:
            alights[plan.alight_idx[r.req_id]] += 1

        new_b = plan.board_idx[new_req.req_id]
        if new_b == BOARDING_NOW:
            return False  # the new passenger always needs a pickup stop
        if snapshot.is_at_capacity:
            first_drop = next((k for k in range(n) if alights[k] > 0), n)
            if new_b < first_drop:
                return False  # capacity lock: no pickup before a drop-off frees room

        # Project load at every stop; the constraint is enforced at each stop where the new passenger is
        # aboard (pickup .. before drop-off) - the only stops whose load the new commitment raises.
        new_a = plan.alight_idx[new_req.req_id]
        load = len(onboard) + now_boarders
        weight = snapshot.current_weight_kg + avg * now_boarders
        for k in range(new_a):
            load -= alights[k]
            weight = max(0.0, weight - avg * alights[k])
            load += boards[k]
            weight += avg * boards[k]
            if k >= new_b and (load > config.max_capacity or weight > w_limit + 1e-9):
                return False
        return True

    def _board_time(self, plan: RoutePlan, times: List[float], t0: float, r: Request) -> float:
        b = plan.board_idx[r.req_id]
        return t0 if b == BOARDING_NOW else times[b]

    def _penalty(self, wait: float) -> float:
        excess = wait - self.soft_max_wait_sec
        return self.fairness_weight * excess * excess if excess > 0 else 0.0

    def _cost(self, ctx: CarContext, new_req: Request, plan: RoutePlan) -> CandidateEvaluation:
        times = self.simulate_route_times(ctx.snapshot, ctx.config, plan.route, ctx.t0)
        new_arrival = times[plan.alight_idx[new_req.req_id]]
        new_board = self._board_time(plan, times, ctx.t0, new_req)
        cost = new_arrival - new_req.timestamp

        base, base_times = ctx.base_plan, ctx.base_times
        for r in ctx.onboard:
            cost += times[plan.alight_idx[r.req_id]] - base_times[base.alight_idx[r.req_id]]
        for r in ctx.waiting:
            cost += times[plan.alight_idx[r.req_id]] - base_times[base.alight_idx[r.req_id]]
            if self.fairness_weight > 0.0:
                new_wait = self._board_time(plan, times, ctx.t0, r) - r.timestamp
                old_wait = self._board_time(base, base_times, ctx.t0, r) - r.timestamp
                cost += self._penalty(new_wait) - self._penalty(old_wait)
        if self.fairness_weight > 0.0:
            cost += self._penalty(new_board - new_req.timestamp)
        return CandidateEvaluation(plan, times, cost, new_board, new_arrival)


def operational_pool(fleet_snapshots: Sequence[SensorSnapshot], max_vibration: float) -> List[SensorSnapshot]:
    """Cars passing the sensor safety gate; prefers cars without a capacity lock when any exist."""
    ok = [s for s in fleet_snapshots if s.is_operational and s.vibration_level <= max_vibration]
    unlocked = [s for s in ok if not s.is_at_capacity]
    return unlocked or ok


def snapshot_map(fleet_snapshots: Sequence[SensorSnapshot]) -> Dict[int, SensorSnapshot]:
    return {s.elevator_id: s for s in fleet_snapshots}
