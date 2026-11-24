"""Single-car physics: kinematics, door cycle, boarding/alighting and the overload interlock (LLD §2.2).

Kinematic model (matches the formal leg-time formula exactly)::

    leg(A -> B) = ACCELERATING (start_time, car still at A)
                + CRUISING     (|B - A| / max_speed, constant velocity)
                + DECELERATING (stop_time, car already level with B)

Stop cycle at a floor::

    STOP logged at arrival  ->  DOOR_OPENING (open_time)  ->  alight, then board one by one
    -> DWELLING (dwell_time) -> [OVERLOADED hold (dwell_time) if a passenger was bumped]
    -> DOOR_CLOSING ([obstruction delay] + close_time) -> IDLE

``step(dt, current_time)`` integrates continuously inside the tick (phase transitions are not snapped
to tick boundaries), so stop timestamps are exact.

Point of no return: while ACCELERATING/CRUISING the earliest floor the car can still stop at is the
first floor strictly ahead of its position (``get_braking_floor``). If the head of the queue is behind
that floor, the car stops at the braking floor without opening its doors (a turnaround) and then
heads for it.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Set

from src.models.dto import ElevatorConfig, Request, SensorSnapshot, StopRecord
from src.models.enums import Direction, ElevatorState, RequestStatus
from src.simulation.sensors import SensorSuite

TIME_EPS = 1e-9
POS_EPS = 1e-6
_MAX_TRANSITIONS_PER_STEP = 10_000

MOVING_STATES = frozenset({ElevatorState.ACCELERATING, ElevatorState.CRUISING, ElevatorState.DECELERATING})
DOOR_STATES = frozenset(
    {ElevatorState.DOOR_OPENING, ElevatorState.DWELLING, ElevatorState.OVERLOADED, ElevatorState.DOOR_CLOSING}
)


class SafetyInterlockError(RuntimeError):
    """Raised if the car is ever asked to depart while overloaded (must never happen)."""


class Elevator:
    def __init__(
        self,
        config: ElevatorConfig,
        sensors: SensorSuite,
        min_floor: int,
        max_floor: int,
        door_obstruction_probability: float = 0.0,
        door_obstruction_delay_sec: float = 0.0,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.config = config
        self.sensors = sensors
        self.min_floor = min_floor
        self.max_floor = max_floor
        self.door_obstruction_probability = door_obstruction_probability
        self.door_obstruction_delay_sec = door_obstruction_delay_sec
        self._rng = rng if rng is not None else random.Random(0)

        self.position: float = float(config.initial_floor)
        self.speed: float = 0.0  # signed, floors/s
        self.direction: Direction = Direction.IDLE
        self.state: ElevatorState = ElevatorState.IDLE
        self.stop_queue: List[int] = []
        self.onboard_requests: List[Request] = []
        self.assigned_waiting_requests: List[Request] = []
        self.at_capacity_flag: bool = False
        self.parking_target: Optional[int] = None

        self.clock: float = 0.0
        self.idle_since: float = 0.0
        self.total_stops: int = 0

        self._leg_target: Optional[int] = None
        self._timer: float = 0.0
        self._obstruction_remaining: float = 0.0
        self._force_next_obstruction: bool = False
        self._overload_this_stop: bool = False
        self._current_stop: Optional[StopRecord] = None
        self._assigned_at: Dict[int, float] = {}  # req_id -> time it was committed to this car

        # Outboxes drained by the SimulationEngine.
        self._pending_bumps: List[Request] = []
        self._stop_outbox: List[StopRecord] = []
        self._completed_outbox: List[Request] = []

    # ================================================================== public API (LLD)

    @property
    def elevator_id(self) -> int:
        return self.config.elevator_id

    @property
    def current_floor(self) -> int:
        """Nearest floor to the cabin's position."""
        return int(round(self.position))

    def assign_request(self, request: Request, updated_route: List[int]) -> None:
        """Commit ``request`` to this car and replace ``stop_queue`` with the dispatcher's route."""
        request.assigned_elevator_id = self.elevator_id
        request.status = RequestStatus.ASSIGNED
        route = list(updated_route)
        self.parking_target = None

        doors_open_here = self.state in (ElevatorState.DOOR_OPENING, ElevatorState.DWELLING)
        if doors_open_here and not self.at_capacity_flag and route and route[0] == self.current_floor:
            route.pop(0)  # merged into the stop that is already in progress
        self.stop_queue = route
        self.assigned_waiting_requests.append(request)
        self._assigned_at[request.req_id] = self.clock

        if (
            self.state == ElevatorState.DWELLING
            and request.src_floor == self.current_floor
            and not self.at_capacity_flag
        ):
            self._board_here([request], self.clock)  # late boarder walks in while doors are open
        self._repair_queue()

    def step(self, dt: float, current_time: float) -> List[Request]:
        """Advance physics from ``current_time`` to ``current_time + dt``.

        Returns requests bumped by the overload interlock (to be re-dispatched immediately).
        """
        t = current_time
        remaining = dt
        transitions = 0
        while remaining > TIME_EPS:
            self.clock = t
            used = self._advance(t, remaining)
            t += used
            remaining -= used
            transitions += 1
            if transitions > _MAX_TRANSITIONS_PER_STEP:
                raise RuntimeError(f"Elevator {self.elevator_id}: no time progress in step()")
        self.clock = current_time + dt
        return self.drain_bumps()

    def get_telemetry(self, current_time: float) -> SensorSnapshot:
        return self.sensors.read_snapshot(
            self.elevator_id,
            current_time,
            self.position,
            self.speed,
            self.get_current_true_weight(),
            self.at_capacity_flag,
        )

    def get_current_true_weight(self) -> float:
        return sum(r.actual_weight_kg for r in self.onboard_requests)

    def get_braking_floor(self) -> int:
        """Nearest floor at which the car can still stop (point of no return)."""
        if self.state in (ElevatorState.ACCELERATING, ElevatorState.CRUISING):
            if self.direction == Direction.UP:
                return min(self.max_floor, math.floor(self.position + POS_EPS) + 1)
            return max(self.min_floor, math.ceil(self.position - POS_EPS) - 1)
        if self.state == ElevatorState.DECELERATING and self._leg_target is not None:
            return self._leg_target
        return self.current_floor

    # ================================================================== engine helpers

    def drain_bumps(self) -> List[Request]:
        bumps, self._pending_bumps = self._pending_bumps, []
        return bumps

    def drain_stop_records(self) -> List[StopRecord]:
        stops, self._stop_outbox = self._stop_outbox, []
        return stops

    def drain_completed(self) -> List[Request]:
        done, self._completed_outbox = self._completed_outbox, []
        return done

    def force_next_door_obstruction(self) -> None:
        """Deterministically obstruct the next door-closing cycle (testing hook)."""
        self._force_next_obstruction = True

    def set_parking_target(self, floor: int) -> None:
        """Reposition an idle, empty car without committing any passenger (no door cycle on arrival)."""
        if self.is_idle and self.min_floor <= floor <= self.max_floor:
            self.parking_target = None if floor == self.current_floor else floor

    @property
    def is_idle(self) -> bool:
        return (
            self.state == ElevatorState.IDLE
            and not self.stop_queue
            and not self.onboard_requests
            and not self.assigned_waiting_requests
        )

    @property
    def active_requests(self) -> List[Request]:
        return sorted(self.onboard_requests + self.assigned_waiting_requests, key=lambda r: r.req_id)

    # ================================================================== state machine

    def _advance(self, t: float, budget: float) -> float:
        """Run the current phase for at most ``budget`` seconds; return the time consumed."""
        s = self.state

        if s == ElevatorState.IDLE:
            if self.stop_queue:
                if self.stop_queue[0] == self.current_floor:
                    self.stop_queue.pop(0)
                    self._begin_stop(t)
                else:
                    self._begin_leg(self.stop_queue[0])
                return 0.0
            if self.parking_target is not None:
                if self.parking_target == self.current_floor:
                    self.parking_target = None
                else:
                    self._begin_leg(self.parking_target)
                    return 0.0
            self.direction = Direction.IDLE
            return budget

        if s == ElevatorState.ACCELERATING:
            self._refresh_leg_target()
            used = min(self._timer, budget)
            self._timer -= used
            if self._timer <= TIME_EPS:
                self.state = ElevatorState.CRUISING
            return used

        if s == ElevatorState.CRUISING:
            self._refresh_leg_target()
            assert self._leg_target is not None
            need = abs(self._leg_target - self.position) / self.config.max_speed
            if need <= budget + TIME_EPS:
                self.position = float(self._leg_target)
                self.speed = 0.0
                self.state = ElevatorState.DECELERATING
                self._timer = self.config.stop_time
                return min(need, budget)
            sign = 1.0 if self.direction == Direction.UP else -1.0
            self.position += sign * self.config.max_speed * budget
            return budget

        if s == ElevatorState.DECELERATING:
            used = min(self._timer, budget)
            self._timer -= used
            if self._timer <= TIME_EPS:
                self._arrive(t + used)
            return used

        if s == ElevatorState.DOOR_OPENING:
            used = min(self._timer, budget)
            self._timer -= used
            if self._timer <= TIME_EPS:
                self._on_doors_open()
                self.state = ElevatorState.DWELLING
                self._timer = self.config.dwell_time
            return used

        if s == ElevatorState.DWELLING:
            used = min(self._timer, budget)
            self._timer -= used
            if self._timer <= TIME_EPS:
                if self._overload_this_stop:
                    self.state = ElevatorState.OVERLOADED  # door held while the excess passenger steps out
                    self._timer = self.config.dwell_time
                else:
                    self._begin_close()
            return used

        if s == ElevatorState.OVERLOADED:
            used = min(self._timer, budget)
            self._timer -= used
            if self._timer <= TIME_EPS:
                self._begin_close()
            return used

        if s == ElevatorState.DOOR_CLOSING:
            if self._obstruction_remaining > TIME_EPS:
                used = min(self._obstruction_remaining, budget)
                self._obstruction_remaining -= used
                if self._obstruction_remaining <= TIME_EPS:
                    self._obstruction_remaining = 0.0
                    self.sensors.inject_door_obstruction(False)
                return used
            used = min(self._timer, budget)
            self._timer -= used
            if self._timer <= TIME_EPS:
                self.state = ElevatorState.IDLE
                self.idle_since = t + used
                self._current_stop = None
            return used

        # OUT_OF_SERVICE (not entered by the current fault model): hold position.
        return budget

    def _begin_leg(self, target: int) -> None:
        weight = self.get_current_true_weight()
        if self.sensors.is_overloaded(weight) or len(self.onboard_requests) > self.config.max_capacity:
            raise SafetyInterlockError(
                f"Elevator {self.elevator_id} attempted to depart with {weight:.1f} kg / "
                f"{len(self.onboard_requests)} passengers"
            )
        self._leg_target = target
        self.direction = Direction.UP if target > self.position else Direction.DOWN
        self.state = ElevatorState.ACCELERATING
        self._timer = self.config.start_time
        self.speed = self.config.max_speed if self.direction == Direction.UP else -self.config.max_speed

    def _refresh_leg_target(self) -> None:
        """Re-aim a moving car at the queue head if it is still reachable, else at the braking floor."""
        desired = self.stop_queue[0] if self.stop_queue else self.parking_target
        braking = self.get_braking_floor()
        if desired is None:
            self._leg_target = braking
        elif (self.direction == Direction.UP and desired >= braking) or (
            self.direction == Direction.DOWN and desired <= braking
        ):
            self._leg_target = desired
        else:
            self._leg_target = braking

    def _arrive(self, t: float) -> None:
        assert self._leg_target is not None
        self.position = float(self._leg_target)
        self.speed = 0.0
        self._leg_target = None
        floor = self.current_floor
        if self.stop_queue and self.stop_queue[0] == floor:
            self.stop_queue.pop(0)
            self._begin_stop(t)
        else:  # parking arrival or turnaround: no door cycle, no STOP record
            if self.parking_target == floor:
                self.parking_target = None
            self.state = ElevatorState.IDLE
            self.idle_since = t

    def _begin_stop(self, t: float) -> None:
        self.state = ElevatorState.DOOR_OPENING
        self.speed = 0.0
        self._timer = self.config.door_open_time
        self._overload_this_stop = False
        self._current_stop = StopRecord(timestamp=t, elevator_id=self.elevator_id, floor=self.current_floor)
        self.total_stops += 1

    def _on_doors_open(self) -> None:
        stop = self._current_stop
        assert stop is not None
        floor = stop.floor
        arrival = stop.timestamp

        alighting = [r for r in self.onboard_requests if r.target_floor == floor]
        for r in alighting:
            self.onboard_requests.remove(r)
            r.arrival_time = arrival
            r.status = RequestStatus.COMPLETED
            stop.alighted_req_ids.append(r.req_id)
            self._completed_outbox.append(r)
        if alighting:
            self.at_capacity_flag = False  # capacity lock released by a drop-off

        boarding = [r for r in self.assigned_waiting_requests if r.src_floor == floor]
        self._board_here(boarding, arrival)
        self._stop_outbox.append(stop)
        self._repair_queue()

    def _board_here(self, candidates: List[Request], board_time: float) -> None:
        """Board candidates one at a time (by req_id); trip the interlock on overweight/headcount."""
        if not candidates:
            return
        if self.at_capacity_flag:
            return  # capacity lock: no pickups until a drop-off
        for r in sorted(candidates, key=lambda r: r.req_id):
            over_weight = self.sensors.is_overloaded(self.get_current_true_weight() + r.actual_weight_kg)
            over_count = len(self.onboard_requests) + 1 > self.config.max_capacity
            if self.at_capacity_flag or over_weight or over_count:
                self._bump(r)
                continue
            self.assigned_waiting_requests.remove(r)
            self.onboard_requests.append(r)
            # a passenger assigned while the doors were already opening boards when assigned, not earlier
            r.board_time = max(board_time, self._assigned_at.pop(r.req_id, board_time))
            r.status = RequestStatus.BOARDED
            if self._current_stop is not None:
                self._current_stop.boarded_req_ids.append(r.req_id)

    def _bump(self, r: Request) -> None:
        self.assigned_waiting_requests.remove(r)
        self._assigned_at.pop(r.req_id, None)
        r.status = RequestStatus.BUMPED_OVERLOAD
        r.assigned_elevator_id = None
        self.at_capacity_flag = True
        self._overload_this_stop = True
        self._pending_bumps.append(r)

    def _begin_close(self) -> None:
        obstructed = self._force_next_obstruction or (
            self.door_obstruction_probability > 0.0 and self._rng.random() < self.door_obstruction_probability
        )
        self._force_next_obstruction = False
        self._obstruction_remaining = 0.0
        if obstructed and self.door_obstruction_delay_sec > 0.0:
            self._obstruction_remaining = self.door_obstruction_delay_sec
            self.sensors.inject_door_obstruction(True)
        self.state = ElevatorState.DOOR_CLOSING
        self._timer = self.config.door_close_time

    def _repair_queue(self) -> None:
        """Guarantee every active request is still served by ``stop_queue`` and drop stops serving nobody.

        Uses the shared route semantics (board at first ``src`` stop, alight at first ``target`` stop after
        boarding). Missing stops are appended at the tail; unused stops (e.g. left behind by a bumped
        passenger) are removed.
        """
        q = self.stop_queue
        used: Set[int] = set()

        def first_index(floor: int, start: int) -> int:
            for k in range(start, len(q)):
                if q[k] == floor:
                    return k
            q.append(floor)
            return len(q) - 1

        boarding_now = self.state == ElevatorState.DOOR_OPENING and not self.at_capacity_flag
        for r in self.onboard_requests:
            used.add(first_index(r.target_floor, 0))
        for r in self.assigned_waiting_requests:
            if boarding_now and r.src_floor == self.current_floor:
                used.add(first_index(r.target_floor, 0))
                continue
            i = first_index(r.src_floor, 0)
            used.add(i)
            used.add(first_index(r.target_floor, i + 1))
        self.stop_queue = [f for k, f in enumerate(q) if k in used]
