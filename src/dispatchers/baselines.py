"""Baseline strategies for benchmarking (allocation_method_design_markdown.md §6)."""
from __future__ import annotations

import time
from typing import Dict, List, Sequence, Tuple

from src.dispatchers.base import IDispatcher
from src.dispatchers.route_evaluator import RouteEvaluator, operational_pool
from src.models.dto import AllocationDecision, BuildingConfig, ElevatorAssignment, Request, SensorSnapshot
from src.models.route_semantics import braking_floor


class _BaselineDispatcher(IDispatcher):
    def initialize(self, building_config: BuildingConfig) -> None:
        self.config = building_config
        self.evaluator = RouteEvaluator.from_config(building_config)
        self.max_vibration = building_config.sensors.max_vibration_threshold

    def _pool(self, fleet_snapshots: List[SensorSnapshot]) -> List[SensorSnapshot]:
        return operational_pool(fleet_snapshots, self.max_vibration) or list(fleet_snapshots)


class FCFSDispatcher(_BaselineDispatcher):
    """First Come, First Served: the operational car that will finish its current queue earliest gets the
    call, with ``(src, target)`` appended to the end of its queue."""

    algorithm_name = "FCFS"

    def allocate(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> AllocationDecision:
        t_start = time.perf_counter()
        best: Tuple[float, int] | None = None
        for snap in self._pool(fleet_snapshots):
            eid = snap.elevator_id
            a = active_assignments[eid]
            ctx = self.evaluator.make_context(snap, self.config.get_elevator_config(eid), a.requests, a.stop_queue, snap.timestamp)
            key = (self.evaluator.queue_completion_time(ctx), eid)
            if best is None or key < best:
                best = key
        assert best is not None
        eid = best[1]
        route = list(active_assignments[eid].stop_queue)
        if not route or route[-1] != request.src_floor:
            route.append(request.src_floor)
        route.append(request.target_floor)
        return AllocationDecision(
            request.req_id, eid, route, estimated_cost=best[0], compute_time_ms=(time.perf_counter() - t_start) * 1e3
        )


def scan_insert(queue: Sequence[int], origin: float, floor: int, direction: int, start: int = 0) -> Tuple[List[int], int]:
    """Insert ``floor`` into the first leg (from index ``start``) that sweeps over it in ``direction``
    (+1 up / -1 down); append at the end of the sweep sequence otherwise. Merges with an equal stop."""
    q = list(queue)
    prev = origin if start == 0 else float(q[start - 1])
    for k in range(start, len(q)):
        nxt = q[k]
        if nxt == floor:
            return q, k
        leg_dir = 1 if nxt > prev else -1 if nxt < prev else 0
        if leg_dir == direction and min(prev, nxt) <= floor <= max(prev, nxt):
            if prev == floor and k > start:
                return q, k - 1
            q.insert(k, floor)
            return q, k
        prev = float(nxt)
    if q and len(q) > start and q[-1] == floor:
        return q, len(q) - 1
    q.append(floor)
    return q, len(q) - 1


class SCANDispatcher(_BaselineDispatcher):
    """Directional sweep (collective control): ``src`` and ``target`` are slotted into the first leg of each
    car's route that sweeps over them in the passenger's travel direction (else appended); the car with the
    shortest estimated pickup time wins."""

    algorithm_name = "SCAN"

    def allocate(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> AllocationDecision:
        t_start = time.perf_counter()
        direction = 1 if request.target_floor > request.src_floor else -1
        best = None
        for snap in self._pool(fleet_snapshots):
            eid = snap.elevator_id
            a = active_assignments[eid]
            origin = float(braking_floor(snap.position, snap.speed)) if snap.speed else snap.position
            q1, si = scan_insert(a.stop_queue, origin, request.src_floor, direction)
            route, _ = scan_insert(q1, origin, request.target_floor, direction, start=si + 1)
            ctx = self.evaluator.make_context(snap, self.config.get_elevator_config(eid), a.requests, a.stop_queue, snap.timestamp)
            ev = self.evaluator.evaluate_unchecked(ctx, request, route)
            key = (ev.new_board_time, eid)
            if best is None or key < best[0]:
                best = (key, eid, route, ev.cost)
        assert best is not None
        _, eid, route, cost = best
        return AllocationDecision(
            request.req_id, eid, route, estimated_cost=cost, compute_time_ms=(time.perf_counter() - t_start) * 1e3
        )
