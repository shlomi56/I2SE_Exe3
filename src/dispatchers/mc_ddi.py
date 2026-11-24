"""Marginal-Cost Destination Dispatch Insertion (allocation_method_design_markdown.md §2).

``allocate`` is a pure function of ``(request, fleet_snapshots, active_assignments, config)`` — the
property the ESP32 ``EdgeAllocator`` must reproduce bit-for-bit. Only idle parking keeps state (a
rolling window of recent call origins), and it never influences ``allocate``.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from typing import Deque, Dict, List, Optional, Tuple

from src.dispatchers.base import IDispatcher
from src.dispatchers.route_evaluator import RouteEvaluator, insert_or_merge, operational_pool
from src.models.dto import AllocationDecision, BuildingConfig, ElevatorAssignment, Request, SensorSnapshot

TIE_EPS = 1e-3  # seconds
DEMAND_WINDOW_SEC = 300.0


class MarginalCostDDDispatcher(IDispatcher):
    algorithm_name = "MC_DDI"

    def initialize(self, building_config: BuildingConfig) -> None:
        self.config = building_config
        self.evaluator = RouteEvaluator.from_config(building_config)
        self.max_vibration = building_config.sensors.max_vibration_threshold
        self._recent_calls: Deque[Tuple[float, int]] = deque()
        self._idle_since: Dict[int, float] = {}
        self._parked: Dict[int, int] = {}  # eid -> floor it was parked at / sent to

    # ================================================================== allocation

    def allocate(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> AllocationDecision:
        t_start = time.perf_counter()
        now = max((s.timestamp for s in fleet_snapshots), default=request.timestamp)
        self._recent_calls.append((now, request.src_floor))

        best_key: Optional[Tuple[float, int, float, int]] = None
        best: Optional[Tuple[int, List[int], float]] = None

        for snap in sorted(fleet_snapshots, key=lambda s: s.elevator_id):
            # 1. sensor & safety gate
            if not snap.is_operational or snap.vibration_level > self.max_vibration:
                continue
            eid = snap.elevator_id
            cfg = self.config.get_elevator_config(eid)
            assignment = active_assignments[eid]
            ctx = self.evaluator.make_context(snap, cfg, assignment.requests, assignment.stop_queue, snap.timestamp)
            base_len = len(ctx.base_plan.route)
            queue = ctx.base_plan.route
            seen: set[Tuple[int, ...]] = set()

            # 2. order-preserving insertions (i <= j) with merging
            for i in range(len(queue) + 1):
                q_src, src_idx = insert_or_merge(queue, request.src_floor, i)
                for j in range(src_idx + 1, len(q_src) + 1):
                    cand, _ = insert_or_merge(q_src, request.target_floor, j, min_merge_index=src_idx + 1)
                    key_route = tuple(cand)
                    if key_route in seen:
                        continue
                    seen.add(key_route)
                    # 3. proactive capacity check + 4. marginal cost
                    ev = self.evaluator.evaluate_candidate(ctx, request, cand)
                    if ev is None:
                        continue
                    added_stops = len(ev.plan.route) - base_len
                    # 5. deterministic tie-breaking: cost, fewer added stops, lighter cabin, lower id
                    if self._is_better(ev.cost, added_stops, snap.current_weight_kg, eid, best_key):
                        best_key = (ev.cost, added_stops, snap.current_weight_kg, eid)
                        best = (eid, list(ev.plan.route), ev.cost)

        # 6. fallback when every car failed the capacity checks
        if best is None:
            best = self._fallback_tail_allocation(request, fleet_snapshots, active_assignments)

        eid, route, cost = best
        return AllocationDecision(
            request.req_id, eid, route, estimated_cost=cost, compute_time_ms=(time.perf_counter() - t_start) * 1e3
        )

    @staticmethod
    def _is_better(
        cost: float, added_stops: int, weight: float, eid: int, best_key: Optional[Tuple[float, int, float, int]]
    ) -> bool:
        if best_key is None:
            return True
        b_cost, b_stops, b_weight, b_eid = best_key
        if cost < b_cost - TIE_EPS:
            return True
        if cost > b_cost + TIE_EPS:
            return False
        return (added_stops, weight, eid) < (b_stops, b_weight, b_eid)

    def _fallback_tail_allocation(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> Tuple[int, List[int], float]:
        """Append ``[src, target]`` to the operational car whose queue empties earliest."""
        pool = operational_pool(fleet_snapshots, self.max_vibration) or list(fleet_snapshots)
        best = None
        for snap in pool:
            eid = snap.elevator_id
            a = active_assignments[eid]
            ctx = self.evaluator.make_context(snap, self.config.get_elevator_config(eid), a.requests, a.stop_queue, snap.timestamp)
            key = (self.evaluator.queue_completion_time(ctx), eid)
            if best is None or key < best[0]:
                best = (key, eid, ctx)
        assert best is not None
        _, eid, ctx = best
        route = list(ctx.base_plan.route)
        if not route or route[-1] != request.src_floor:
            route.append(request.src_floor)
        route.append(request.target_floor)
        ev = self.evaluator.evaluate_unchecked(ctx, request, route)
        return eid, route, ev.cost

    # ================================================================== idle parking

    def get_parking_targets(
        self,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
        current_time: float,
    ) -> Dict[int, int]:
        """Send cars idle for longer than ``idle_parking_timeout_sec`` toward the busiest recent call
        origins (one car per floor, highest demand first, nearest idle car)."""
        settings = self.config.dispatcher
        if not settings.enable_idle_parking:
            return {}
        while self._recent_calls and self._recent_calls[0][0] < current_time - DEMAND_WINDOW_SEC:
            self._recent_calls.popleft()

        ready: Dict[int, SensorSnapshot] = {}
        for snap in fleet_snapshots:
            eid = snap.elevator_id
            a = active_assignments[eid]
            idle = snap.speed == 0.0 and not a.stop_queue and not a.requests and snap.is_operational
            if not idle:
                self._idle_since.pop(eid, None)
                self._parked.pop(eid, None)
                continue
            since = self._idle_since.setdefault(eid, current_time)
            if eid not in self._parked and current_time - since >= settings.idle_parking_timeout_sec:
                ready[eid] = snap
        if not ready or not self._recent_calls:
            return {}

        demand = Counter(floor for _, floor in self._recent_calls)
        floors = sorted(demand, key=lambda f: (-demand[f], f))
        occupied = {
            int(round(s.position)) for s in fleet_snapshots if s.elevator_id not in ready and s.speed == 0.0
        } | set(self._parked.values())
        targets: Dict[int, int] = {}
        for floor in floors:
            if not ready:
                break
            if floor in occupied:
                continue
            eid = min(ready, key=lambda e: (abs(ready[e].position - floor), e))
            snap = ready.pop(eid)
            self._parked[eid] = floor
            occupied.add(floor)
            if int(round(snap.position)) != floor:
                targets[eid] = floor
        return targets
