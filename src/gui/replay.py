"""Replay capture for the GUI (no Streamlit imports — usable and testable headlessly).

``SimulationRecorder`` is an ``ISimulationObserver`` that samples the building every ``sample_dt``
seconds into immutable :class:`Frame` objects and logs discrete events. ``record_simulation`` runs a
headless ``SimulationEngine`` with the recorder attached and returns a :class:`Replay` the dashboard
can scrub through. The GUI only ever *reads* the simulation; nothing flows back into the core.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from src.dispatchers import create_dispatcher
from src.models.dto import AllocationDecision, BuildingConfig, Request, SensorSnapshot, StopRecord
from src.models.enums import ElevatorState
from src.simulation import SimulationEngine
from src.simulation.observer import ISimulationObserver

# GUI-level car status (derived from ElevatorState + sensors)
STATUS_MOVING = "MOVING"
STATUS_DOORS_OPEN = "DOORS_OPEN"
STATUS_OVERLOADED = "OVERLOADED"
STATUS_FAULT = "FAULT"
STATUS_IDLE = "IDLE"
STATUSES = (STATUS_MOVING, STATUS_DOORS_OPEN, STATUS_OVERLOADED, STATUS_FAULT, STATUS_IDLE)

_MOVING = {ElevatorState.ACCELERATING, ElevatorState.CRUISING, ElevatorState.DECELERATING}
_DOORS = {ElevatorState.DOOR_OPENING, ElevatorState.DWELLING, ElevatorState.DOOR_CLOSING}


def car_status(state: ElevatorState, operational: bool) -> str:
    if not operational:
        return STATUS_FAULT
    if state == ElevatorState.OVERLOADED:
        return STATUS_OVERLOADED
    if state in _MOVING:
        return STATUS_MOVING
    if state in _DOORS:
        return STATUS_DOORS_OPEN
    return STATUS_IDLE


@dataclass(frozen=True)
class CarFrame:
    elevator_id: int
    position: float
    speed: float
    state: str
    status: str
    direction: str  # "UP" | "DOWN" | "IDLE"
    passengers: int
    capacity: int
    weight_kg: float
    max_weight_kg: float
    door_obstructed: bool
    vibration: float
    operational: bool
    at_capacity: bool
    stop_queue: Tuple[int, ...]


@dataclass(frozen=True)
class Frame:
    time: float
    cars: Tuple[CarFrame, ...]
    waiting: Dict[int, Tuple[Tuple[int, int, Optional[int]], ...]]  # floor -> ((req_id, target, elevator_id), ...)


@dataclass(frozen=True)
class SimEvent:
    time: float
    kind: str  # ALLOC | REALLOC | OVERLOAD | DOOR_OBSTRUCTION | FAULT_START | FAULT_END
    elevator_id: Optional[int]
    detail: str


@dataclass
class Replay:
    algorithm: str
    scenario: str
    seed: int
    min_floor: int
    max_floor: int
    lobby_floor: int
    sample_dt: float
    frames: List[Frame]
    events: List[SimEvent]
    completions: List[Tuple[float, float, float]]  # (arrival_time, journey, wait), sorted by arrival
    stop_times: List[float]
    overload_times: List[float]
    fault_windows: List[Tuple[int, str, float, float, float]]  # (elevator_id, type, start, end, severity)
    summary: dict
    _frame_times: List[float] = field(default_factory=list, repr=False)
    _event_times: List[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._frame_times = [f.time for f in self.frames]
        self.events.sort(key=lambda e: e.time)
        self._event_times = [e.time for e in self.events]
        self.completions.sort()
        self.stop_times.sort()
        self.overload_times.sort()

    @property
    def end_time(self) -> float:
        return self.frames[-1].time if self.frames else 0.0

    def frame_at(self, t: float) -> Frame:
        """Latest recorded frame at or before ``t``."""
        i = bisect_right(self._frame_times, t + 1e-9) - 1
        return self.frames[max(i, 0)]

    def kpis_at(self, t: float) -> dict:
        """Running KPIs over passengers delivered by time ``t``."""
        n = bisect_right(self.completions, (t + 1e-9, float("inf"), float("inf")))
        done = self.completions[:n]
        journeys = [c[1] for c in done]
        waits = [c[2] for c in done]
        return {
            "completed": n,
            "total": self.summary.get("total_requests", 0),
            "avg_journey": sum(journeys) / n if n else 0.0,
            "avg_wait": sum(waits) / n if n else 0.0,
            "max_wait": max(waits) if n else 0.0,
            "stops": bisect_right(self.stop_times, t + 1e-9),
            "overloads": bisect_right(self.overload_times, t + 1e-9),
        }

    def events_between(self, t0: float, t1: float) -> List[SimEvent]:
        lo = bisect_right(self._event_times, t0 - 1e-9)
        hi = bisect_right(self._event_times, t1 + 1e-9)
        return self.events[lo:hi]

    def active_faults(self, t: float) -> List[Tuple[int, str, float, float, float]]:
        return [w for w in self.fault_windows if w[2] <= t < w[3]]

    def journey_curve(self, step_sec: float = 60.0) -> List[Tuple[float, float]]:
        """Running average journey time sampled every ``step_sec`` (for the over-time chart)."""
        out: List[Tuple[float, float]] = []
        t = step_sec
        while t <= self.end_time + step_sec:
            k = self.kpis_at(t)
            if k["completed"]:
                out.append((t, k["avg_journey"]))
            t += step_sec
        return out


class SimulationRecorder(ISimulationObserver):
    """Samples the building into frames and records discrete events (read-only access to the core)."""

    def __init__(self, building, sample_dt: float = 0.5) -> None:
        self.building = building
        self.sample_dt = sample_dt
        self.frames: List[Frame] = []
        self.events: List[SimEvent] = []
        self.stop_times: List[float] = []
        self.overload_times: List[float] = []
        self._next_sample = 0.0
        self._last_bump: Dict[int, float] = {}
        self._obstructed: Dict[int, bool] = {}
        self._last_time = 0.0

    # -------------------------------------------------------------- hooks

    def on_request_allocated(self, request: Request, decision: AllocationDecision) -> None:
        if request.req_id in self._last_bump:
            t = self._last_bump.pop(request.req_id)
            self.events.append(SimEvent(t, "REALLOC", decision.elevator_id,
                                        f"Passenger #{request.req_id} re-dispatched to E{decision.elevator_id}"))
        else:
            self.events.append(SimEvent(request.timestamp, "ALLOC", decision.elevator_id,
                                        f"#{request.req_id} {request.src_floor}→{request.target_floor} assigned to E{decision.elevator_id}"))

    def on_elevator_stop(self, stop_record: StopRecord) -> None:
        self.stop_times.append(stop_record.timestamp)

    def on_overload_detected(self, elevator_id: int, bumped_request: Request, timestamp: float) -> None:
        self._last_bump[bumped_request.req_id] = timestamp
        self.overload_times.append(timestamp)
        self.events.append(SimEvent(timestamp, "OVERLOAD", elevator_id,
                                    f"E{elevator_id} overload: passenger #{bumped_request.req_id} "
                                    f"({bumped_request.actual_weight_kg:.0f} kg) stepped out"))

    def on_tick(self, current_time: float, fleet_snapshots: List[SensorSnapshot]) -> None:
        self._last_time = current_time
        for s in fleet_snapshots:
            was = self._obstructed.get(s.elevator_id, False)
            if s.door_obstructed and not was:
                self.events.append(SimEvent(current_time, "DOOR_OBSTRUCTION", s.elevator_id,
                                            f"E{s.elevator_id} door obstructed"))
            self._obstructed[s.elevator_id] = s.door_obstructed
        if current_time + 1e-9 >= self._next_sample:
            self.capture(current_time, fleet_snapshots)
            self._next_sample = current_time + self.sample_dt

    def on_simulation_complete(self, summary_metrics: dict) -> None:
        if not self.frames or self.frames[-1].time < self._last_time:
            self.capture(self._last_time, self.building.get_fleet_telemetry(self._last_time))

    # -------------------------------------------------------------- capture

    def capture(self, t: float, snapshots: Sequence[SensorSnapshot]) -> None:
        by_id = {s.elevator_id: s for s in snapshots}
        cars = []
        for eid, e in self.building.elevators.items():
            s = by_id[eid]
            if e.state in _MOVING:
                direction = e.direction.value
            elif e.stop_queue:
                direction = "UP" if e.stop_queue[0] > e.position else "DOWN" if e.stop_queue[0] < e.position else "IDLE"
            else:
                direction = "IDLE"
            cars.append(CarFrame(
                elevator_id=eid,
                position=round(e.position, 3),
                speed=s.speed,
                state=e.state.value,
                status=car_status(e.state, s.is_operational),
                direction=direction,
                passengers=len(e.onboard_requests),
                capacity=e.config.max_capacity,
                weight_kg=round(s.current_weight_kg, 1),
                max_weight_kg=e.config.max_weight_kg,
                door_obstructed=s.door_obstructed,
                vibration=s.vibration_level,
                operational=s.is_operational,
                at_capacity=s.is_at_capacity,
                stop_queue=tuple(e.stop_queue),
            ))
        waiting = {
            floor: tuple((r.req_id, r.target_floor, r.assigned_elevator_id) for r in q)
            for floor, q in self.building.floor_queues.items()
            if q
        }
        self.frames.append(Frame(round(t, 3), tuple(cars), waiting))


def record_simulation(
    config: BuildingConfig,
    requests: List[Request],
    algorithm: str,
    scenario: str = "custom",
    sample_dt: float = 0.5,
) -> Replay:
    """Run a headless simulation with a recorder attached and return the replay."""
    engine = SimulationEngine(config, create_dispatcher(algorithm), requests)
    rec = SimulationRecorder(engine.building, sample_dt=sample_dt)
    engine.register_observer(rec)
    rec.capture(0.0, engine.building.get_fleet_telemetry(0.0))
    rec._next_sample = sample_dt
    summary = engine.run()

    events = list(rec.events)
    for f in config.sensors.injected_faults:
        label = "Vibration fault" if f.type == "VIBRATION_FAULT" else "Out of service"
        events.append(SimEvent(f.start_time_sec, "FAULT_START", f.elevator_id,
                               f"{label} on E{f.elevator_id} (severity {f.severity:g})"))
        events.append(SimEvent(f.end_time_sec, "FAULT_END", f.elevator_id, f"E{f.elevator_id} back in service"))

    completions = [
        (r.arrival_time, r.arrival_time - r.timestamp, r.board_time - r.timestamp)
        for r in engine.requests
        if r.arrival_time is not None and r.board_time is not None
    ]
    return Replay(
        algorithm=algorithm,
        scenario=scenario,
        seed=config.simulation.random_seed,
        min_floor=config.min_floor,
        max_floor=config.max_floor,
        lobby_floor=config.lobby_floor,
        sample_dt=sample_dt,
        frames=rec.frames,
        events=events,
        completions=completions,
        stop_times=rec.stop_times,
        overload_times=rec.overload_times,
        fault_windows=[(f.elevator_id, f.type, f.start_time_sec, f.end_time_sec, f.severity)
                       for f in config.sensors.injected_faults],
        summary=summary,
    )
