"""Route semantics shared by the physics engine and every dispatcher (single source of truth).

* A car serves its ``stop_queue`` strictly in order.
* A waiting request boards at the **first** stop at its ``src_floor``; it alights at the first stop at
  its ``target_floor`` after boarding. An onboard request alights at the first stop at its target.
* A stop that serves no request is redundant and is pruned.

Because the engine (``Elevator``) and the dispatchers (``RouteEvaluator``) resolve routes with the same
function, the dispatcher's model of who boards/alights where is exactly what the simulator executes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from src.models.dto import Request
from src.models.enums import RequestStatus

BOARDING_NOW = -1  # board index of a request that boards at the car's current position immediately
_POS_EPS = 1e-6


@dataclass
class RoutePlan:
    route: List[int]
    board_idx: Dict[int, int] = field(default_factory=dict)  # req_id -> stop index (or BOARDING_NOW)
    alight_idx: Dict[int, int] = field(default_factory=dict)  # req_id -> stop index


def plan_route(
    route: Sequence[int],
    onboard: Sequence[Request],
    waiting: Sequence[Request],
    boarding_now: Optional[Callable[[Request], bool]] = None,
    append_missing_src: bool = False,
    prune: bool = True,
) -> RoutePlan:
    """Resolve where every request boards/alights on ``route``.

    Missing targets are always appended at the tail. A waiting request whose ``src`` is absent is either
    appended (``append_missing_src=True``, the engine's repair mode) or treated as boarding immediately at
    the car's current position (dispatcher view: that only happens while the doors are opening there).
    With ``prune=True`` unused stops are removed and indices refer to the pruned route.
    """
    q = list(route)
    used: set[int] = set()
    board: Dict[int, int] = {}
    alight: Dict[int, int] = {}

    def find(floor: int, start: int) -> Optional[int]:
        for k in range(start, len(q)):
            if q[k] == floor:
                return k
        return None

    def find_or_append(floor: int, start: int) -> int:
        k = find(floor, start)
        if k is None:
            q.append(floor)
            k = len(q) - 1
        return k

    for r in onboard:
        a = find_or_append(r.target_floor, 0)
        alight[r.req_id] = a
        used.add(a)
    for r in waiting:
        if boarding_now is not None and boarding_now(r):
            b = BOARDING_NOW
        else:
            found = find(r.src_floor, 0)
            if found is not None:
                b = found
            elif append_missing_src:
                b = find_or_append(r.src_floor, 0)
            else:
                b = BOARDING_NOW
        if b != BOARDING_NOW:
            used.add(b)
        a = find_or_append(r.target_floor, b + 1)
        board[r.req_id] = b
        alight[r.req_id] = a
        used.add(a)

    if not prune:
        return RoutePlan(q, board, alight)
    keep = sorted(used)
    remap = {old: new for new, old in enumerate(keep)}
    remap[BOARDING_NOW] = BOARDING_NOW
    return RoutePlan(
        [q[k] for k in keep],
        {rid: remap[k] for rid, k in board.items()},
        {rid: remap[k] for rid, k in alight.items()},
    )


def braking_floor(position: float, signed_speed: float) -> int:
    """Earliest floor a car can still stop at: the first floor strictly ahead while moving, else the
    nearest floor (the car's own floor when stationary or already decelerating onto a floor)."""
    if signed_speed > 0:
        return math.floor(position + _POS_EPS) + 1
    if signed_speed < 0:
        return math.ceil(position - _POS_EPS) - 1
    return int(round(position))


def split_active(requests: Sequence[Request]) -> tuple[List[Request], List[Request]]:
    """Split a car's active requests into (onboard, waiting) by status."""
    onboard = [r for r in requests if r.status == RequestStatus.BOARDED]
    waiting = [r for r in requests if r.status != RequestStatus.BOARDED]
    return onboard, waiting
