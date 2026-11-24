"""``SimulationEngine`` — clock, call dispatch, physics ticks and overload recovery (LLD §4.4).

Clock model (Phase 1 audit item 8): the engine advances in fixed ``dt`` ticks counted as integers.
Within a tick, elevators are advanced exactly to each request's timestamp before that request is
dispatched, so calls are allocated at their true call time and every event timestamp is exact.
Observers' ``on_tick`` fires at every tick boundary.
"""
from __future__ import annotations

import dataclasses
import time
from collections import defaultdict
from typing import Dict, List, Optional

from src.dispatchers.base import IDispatcher
from src.models.dto import AllocationDecision, BuildingConfig, InjectedFault, Request
from src.models.enums import RequestStatus
from src.simulation.building import Building
from src.simulation.logger import build_summary
from src.simulation.observer import ISimulationObserver

_MAX_REDISPATCH_ROUNDS = 1000


class DispatchError(RuntimeError):
    """Raised when a dispatcher returns an unusable decision."""


class SimulationEngine:
    def __init__(
        self,
        config: BuildingConfig,
        dispatcher: IDispatcher,
        requests: List[Request],
        building: Optional[Building] = None,
        max_sim_time_sec: Optional[float] = None,
    ) -> None:
        self.config = config
        self.building = building if building is not None else Building(config)
        self.dispatcher = dispatcher
        self.observers: List[ISimulationObserver] = []
        self.dt: float = config.simulation.time_step_dt_sec
        self.current_time: float = 0.0
        self.max_sim_time_sec = (
            max_sim_time_sec if max_sim_time_sec is not None else config.simulation.duration_sec + 7200.0
        )

        # Fresh copies so the caller's list can be reused across runs/algorithms.
        self.requests: List[Request] = [
            dataclasses.replace(
                r, assigned_elevator_id=None, board_time=None, arrival_time=None, status=RequestStatus.PENDING
            )
            for r in sorted(requests, key=lambda r: (r.timestamp, r.req_id))
        ]
        for r in self.requests:
            if not (self.building.is_valid_floor(r.src_floor) and self.building.is_valid_floor(r.target_floor)):
                raise ValueError(f"Request {r.req_id} has a floor outside the building: {r.src_floor}->{r.target_floor}")
            if r.src_floor == r.target_floor:
                raise ValueError(f"Request {r.req_id} has src == target ({r.src_floor})")

        self._tick = 0
        self._next_req = 0
        self._completed = 0
        self.overload_events = 0
        self.dispatch_latencies_ms: List[float] = []
        self.allocation_log: List[tuple[float, int, int, str]] = []  # (time, req_id, elevator_id, ALLOC|REALLOC)
        self.stalled = False
        self.summary: Optional[dict] = None

        self._faults: Dict[int, List[InjectedFault]] = defaultdict(list)
        for f in config.sensors.injected_faults:
            self._faults[f.elevator_id].append(f)

        self.dispatcher.initialize(config)
        self._apply_faults(0.0)

    # ================================================================== public API

    def register_observer(self, observer: ISimulationObserver) -> None:
        self.observers.append(observer)

    def run(self, realtime_scale: float = 0.0) -> dict:
        """Run to completion (``0.0`` = headless max speed; ``s > 0`` sleeps ``dt / s`` per tick)."""
        while self.step_once():
            if realtime_scale > 0.0:
                time.sleep(self.dt / realtime_scale)
        return self.finalize()

    def step_once(self) -> bool:
        """Advance one ``dt`` tick. Returns ``False`` once every request is completed (or on stall)."""
        if self.is_finished():
            return False
        t0 = self.current_time
        t1 = round((self._tick + 1) * self.dt, 9)
        cursor = t0

        while self._next_req < len(self.requests) and self.requests[self._next_req].timestamp <= t1:
            req = self.requests[self._next_req]
            self._next_req += 1
            target_t = max(req.timestamp, cursor)
            if target_t > cursor:
                self._advance_elevators(cursor, target_t - cursor)
                cursor = target_t
            self._apply_faults(cursor)
            self._dispatch(req, cursor, realloc=False)
            self._redispatch_bumps(cursor)

        self._advance_elevators(cursor, t1 - cursor)
        self._tick += 1
        self.current_time = t1
        self._apply_faults(t1)

        snapshots = self.building.get_fleet_telemetry(t1)
        self.dispatcher.notify_state_update(snapshots)
        if self.config.dispatcher.enable_idle_parking:
            targets = self.dispatcher.get_parking_targets(snapshots, self.building.get_active_assignments(), t1)
            for eid, floor in targets.items():
                self.building.get_elevator(eid).set_parking_target(floor)
        self.building.sync_floor_queues()
        for obs in self.observers:
            obs.on_tick(t1, snapshots)

        if self.current_time >= self.max_sim_time_sec and not self.is_finished():
            self.stalled = True
        return not self.is_finished()

    def is_finished(self) -> bool:
        all_done = self._next_req >= len(self.requests) and self._completed >= len(self.requests)
        return all_done or self.stalled

    def finalize(self) -> dict:
        """Compute KPIs and notify observers (idempotent)."""
        if self.summary is None:
            self.summary = build_summary(
                self.config.simulation.name,
                self.dispatcher.algorithm_name,
                self.dispatcher.execution_mode,
                self.requests,
                sum(e.total_stops for e in self.building.elevators.values()),
                self.overload_events,
                self.dispatch_latencies_ms,
            )
            for obs in self.observers:
                obs.on_simulation_complete(self.summary)
        return self.summary

    # ================================================================== internals

    def _advance_elevators(self, start: float, dt: float) -> None:
        end = start + dt
        bumps: List[tuple[int, Request]] = []
        for eid, elevator in self.building.elevators.items():
            for r in elevator.step(dt, start):
                bumps.append((eid, r))
            self._collect_outboxes(elevator)
        for eid, r in bumps:
            self._handle_bump(eid, r, end)
        if bumps:
            self._redispatch_bumps(end)

    def _collect_outboxes(self, elevator) -> None:
        for stop in elevator.drain_stop_records():
            for obs in self.observers:
                obs.on_elevator_stop(stop)
        self._completed += len(elevator.drain_completed())

    def _handle_bump(self, elevator_id: int, req: Request, now: float) -> None:
        self.overload_events += 1
        self.building.add_waiting_passenger(req)
        for obs in self.observers:
            obs.on_overload_detected(elevator_id, req, now)
        self._dispatch(req, now, realloc=True)

    def _redispatch_bumps(self, now: float) -> None:
        """Bumps can cascade (a re-dispatched passenger may itself trip another car's interlock)."""
        for _ in range(_MAX_REDISPATCH_ROUNDS):
            found = False
            for eid, elevator in self.building.elevators.items():
                for r in elevator.drain_bumps():
                    found = True
                    self._handle_bump(eid, r, now)
                self._collect_outboxes(elevator)
            if not found:
                return
        raise DispatchError("Overload re-dispatch did not converge")

    def _dispatch(self, req: Request, now: float, realloc: bool) -> AllocationDecision:
        snapshots = self.building.get_fleet_telemetry(now)
        assignments = self.building.get_active_assignments()
        t_start = time.perf_counter()
        decision = self.dispatcher.allocate(req, snapshots, assignments)
        self.dispatch_latencies_ms.append((time.perf_counter() - t_start) * 1000.0)

        if decision.req_id != req.req_id:
            raise DispatchError(f"Decision for req {decision.req_id} returned while allocating {req.req_id}")
        if decision.elevator_id not in self.building.elevators:
            raise DispatchError(f"Dispatcher chose unknown elevator {decision.elevator_id}")
        bad = [f for f in decision.planned_route if not self.building.is_valid_floor(f)]
        if bad:
            raise DispatchError(f"Planned route contains invalid floors {bad}")

        self.building.add_waiting_passenger(req)
        self.building.get_elevator(decision.elevator_id).assign_request(req, decision.planned_route)
        self.allocation_log.append((now, req.req_id, decision.elevator_id, "REALLOC" if realloc else "ALLOC"))
        for obs in self.observers:
            obs.on_request_allocated(req, decision)
        return decision

    def _apply_faults(self, t: float) -> None:
        for eid, faults in self._faults.items():
            elevator = self.building.elevators.get(eid)
            if elevator is None:
                continue
            active = [f for f in faults if f.is_active(t)]
            vib = max((f.severity for f in active if f.type == "VIBRATION_FAULT"), default=0.0)
            elevator.sensors.inject_vibration_fault(vib)
            elevator.sensors.set_fault_active(any(f.type == "OUT_OF_SERVICE" for f in active))
