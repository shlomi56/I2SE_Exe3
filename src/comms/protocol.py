"""PC-side protocol codec — newline-delimited JSON, one object per line.

Messages (``type`` key):

* ``INIT_CONFIG``  PC -> ESP32  ``{"type","v","config":{floors, MC-DDI parameters, "elevators":[...]}}``
* ``INIT_ACK``     ESP32 -> PC  ``{"type","elevators"}``            (acknowledges INIT_CONFIG)
* ``ALLOCATE_REQ`` PC -> ESP32  ``{"type","req":{"id","time","src","target"},"fleet_state":[...]}``
* ``ALLOCATE_RES`` ESP32 -> PC  ``{"type","req_id","elevator_id","route","cost","compute_ms"}``
* ``ERROR``        ESP32 -> PC  ``{"type","req_id","code","message"}``
* ``PING``/``PONG``             heartbeat

``fleet_state`` entries: ``{"id","t","pos","spd","w","vib","obs","op","cap","queue":[floors],
"reqs":[[req_id, call_time, src, target, onboard(0|1)], ...]}``. Hidden passenger weights are never sent.
Floats use Python's shortest round-trip ``repr``, so the edge sees bit-identical values.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from src.models.dto import AllocationDecision, BuildingConfig, ElevatorAssignment, Request, SensorSnapshot
from src.models.enums import RequestStatus

PROTOCOL_VERSION = 1
MESSAGE_TYPES = ("INIT_CONFIG", "INIT_ACK", "ALLOCATE_REQ", "ALLOCATE_RES", "ERROR", "PING", "PONG")


class ProtocolError(ValueError):
    """Malformed or unexpected message."""


def _dumps(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False)


def encode_init_config(config: BuildingConfig) -> str:
    return _dumps(
        {
            "type": "INIT_CONFIG",
            "v": PROTOCOL_VERSION,
            "config": {
                "min_floor": config.min_floor,
                "max_floor": config.max_floor,
                "avg_weight_kg": config.avg_passenger_weight_kg,
                "weight_buffer_ratio": config.weight_buffer_ratio,
                "soft_max_wait_sec": config.dispatcher.soft_max_wait_sec,
                "fairness_weight": config.dispatcher.fairness_weight,
                "door_obstruction_delay_sec": config.sensors.door_obstruction_delay_sec,
                "max_vibration": config.sensors.max_vibration_threshold,
                "elevators": [
                    {
                        "id": e.elevator_id,
                        "speed": e.max_speed,
                        "start": e.start_time,
                        "stop": e.stop_time,
                        "open": e.door_open_time,
                        "dwell": e.dwell_time,
                        "close": e.door_close_time,
                        "max_w": e.max_weight_kg,
                        "cap": e.max_capacity,
                    }
                    for e in sorted(config.elevators, key=lambda e: e.elevator_id)
                ],
            },
        }
    )


def encode_fleet_state(
    fleet_snapshots: List[SensorSnapshot], active_assignments: Dict[int, ElevatorAssignment]
) -> List[Dict[str, Any]]:
    out = []
    for s in sorted(fleet_snapshots, key=lambda s: s.elevator_id):
        a = active_assignments.get(s.elevator_id, ElevatorAssignment())
        out.append(
            {
                "id": s.elevator_id,
                "t": s.timestamp,
                "pos": s.position,
                "spd": s.speed,
                "w": s.current_weight_kg,
                "vib": s.vibration_level,
                "obs": 1 if s.door_obstructed else 0,
                "op": 1 if s.is_operational else 0,
                "cap": 1 if s.is_at_capacity else 0,
                "queue": list(a.stop_queue),
                "reqs": [
                    [r.req_id, r.timestamp, r.src_floor, r.target_floor, 1 if r.status == RequestStatus.BOARDED else 0]
                    for r in a.requests
                ],
            }
        )
    return out


def encode_allocate_req(
    request: Request, fleet_snapshots: List[SensorSnapshot], active_assignments: Dict[int, ElevatorAssignment]
) -> str:
    return _dumps(
        {
            "type": "ALLOCATE_REQ",
            "req": {"id": request.req_id, "time": request.timestamp, "src": request.src_floor, "target": request.target_floor},
            "fleet_state": encode_fleet_state(fleet_snapshots, active_assignments),
        }
    )


def encode_allocate_res(decision: AllocationDecision) -> str:
    return _dumps(
        {
            "type": "ALLOCATE_RES",
            "req_id": decision.req_id,
            "elevator_id": decision.elevator_id,
            "route": list(decision.planned_route),
            "cost": decision.estimated_cost,
            "compute_ms": decision.compute_time_ms,
        }
    )


def encode_ping() -> str:
    return _dumps({"type": "PING"})


def decode_message(line: str) -> Dict[str, Any]:
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProtocolError(f"not valid JSON: {line[:80]!r}") from exc
    if not isinstance(msg, dict) or msg.get("type") not in MESSAGE_TYPES:
        raise ProtocolError(f"unknown message: {line[:80]!r}")
    return msg


def decode_allocate_res(msg: Dict[str, Any]) -> AllocationDecision:
    if msg.get("type") != "ALLOCATE_RES":
        raise ProtocolError(f"expected ALLOCATE_RES, got {msg.get('type')}")
    try:
        route = [int(f) for f in msg["route"]]
        return AllocationDecision(
            req_id=int(msg["req_id"]),
            elevator_id=int(msg["elevator_id"]),
            planned_route=route,
            estimated_cost=float(msg["cost"]),
            compute_time_ms=float(msg.get("compute_ms", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(f"malformed ALLOCATE_RES: {msg}") from exc
