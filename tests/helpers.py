"""Test-only dispatchers and observers (the real FCFS/SCAN/MC-DDI strategies arrive in Phase 4)."""
from __future__ import annotations

from typing import Dict, List, Optional

from src.dispatchers.base import IDispatcher
from src.models.dto import AllocationDecision, BuildingConfig, ElevatorAssignment, Request, SensorSnapshot
from src.simulation.elevator import MOVING_STATES
from src.simulation.observer import ISimulationObserver


class TailAppendDispatcher(IDispatcher):
    """Shortest-queue car that is operational (and not capacity-locked); appends ``(src, target)`` at the tail.

    ``forced_elevator`` pins every *first* allocation to one car (re-allocations still choose freely),
    which lets tests drive a burst of heavy passengers into the same cabin.
    """

    algorithm_name = "TEST_TAIL"

    def __init__(self, forced_elevator: Optional[int] = None) -> None:
        self.forced_elevator = forced_elevator
        self.seen: set[int] = set()

    def initialize(self, building_config: BuildingConfig) -> None:
        self.config = building_config

    def allocate(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> AllocationDecision:
        first_time = request.req_id not in self.seen
        self.seen.add(request.req_id)
        if first_time and self.forced_elevator is not None:
            eid = self.forced_elevator
        else:
            pool = [s for s in fleet_snapshots if s.is_operational and not s.is_at_capacity]
            pool = pool or [s for s in fleet_snapshots if s.is_operational] or fleet_snapshots
            eid = min(pool, key=lambda s: (len(active_assignments[s.elevator_id].stop_queue), s.elevator_id)).elevator_id
        route = list(active_assignments[eid].stop_queue)
        if not route or route[-1] != request.src_floor:
            route.append(request.src_floor)
        route.append(request.target_floor)
        return AllocationDecision(request.req_id, eid, route, estimated_cost=0.0)


class RecordingObserver(ISimulationObserver):
    """Records every hook call and checks the motion safety invariant on every tick."""

    def __init__(self, building) -> None:
        self.building = building
        self.allocations: List[tuple] = []
        self.stops: List = []
        self.overloads: List[tuple] = []
        self.ticks = 0
        self.snapshots_by_tick: List[tuple] = []
        self.keep_snapshots = False
        self.summary: Optional[dict] = None
        self.safety_violations: List[str] = []

    def on_request_allocated(self, request, decision):
        self.allocations.append((request.req_id, decision.elevator_id, list(decision.planned_route)))

    def on_elevator_stop(self, stop_record):
        self.stops.append(stop_record)

    def on_overload_detected(self, elevator_id, bumped_request, timestamp):
        self.overloads.append((elevator_id, bumped_request.req_id, timestamp))

    def on_tick(self, current_time, fleet_snapshots):
        self.ticks += 1
        if self.keep_snapshots:
            self.snapshots_by_tick.append((current_time, fleet_snapshots))
        for e in self.building.elevators.values():
            if e.state in MOVING_STATES:
                w = e.get_current_true_weight()
                if w > e.config.max_weight_kg or len(e.onboard_requests) > e.config.max_capacity:
                    self.safety_violations.append(f"t={current_time} car {e.elevator_id}: {w} kg moving")

    def on_simulation_complete(self, summary_metrics):
        self.summary = summary_metrics
