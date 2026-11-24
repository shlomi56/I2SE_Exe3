"""Core enumerations (LLD §1.1).

All enums subclass ``str`` so they serialize to JSON / CSV as their plain names.
"""
from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    IDLE = "IDLE"


class ElevatorState(str, Enum):
    IDLE = "IDLE"
    ACCELERATING = "ACCELERATING"
    CRUISING = "CRUISING"
    DECELERATING = "DECELERATING"
    DOOR_OPENING = "DOOR_OPENING"
    DWELLING = "DWELLING"
    DOOR_CLOSING = "DOOR_CLOSING"
    OVERLOADED = "OVERLOADED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    BOARDED = "BOARDED"
    COMPLETED = "COMPLETED"
    BUMPED_OVERLOAD = "BUMPED_OVERLOAD"


class TrafficType(str, Enum):
    """Origin–destination category sampled by the request generator (generator spec §2.4)."""

    UP_PEAK = "UP_PEAK"
    DOWN_PEAK = "DOWN_PEAK"
    INTER_FLOOR = "INTER_FLOOR"
