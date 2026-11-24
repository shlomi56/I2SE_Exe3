"""Fail-fast parser for ``simulation_config.json`` (simulation_configuration_documentation.md §2).

Every violated rule raises :class:`ConfigValidationError` naming the offending JSON path, before
any simulation or request generation starts.
"""
from __future__ import annotations

import copy
import json
import math
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.models.dto import (
    BuildingConfig,
    DispatcherSettings,
    ElevatorConfig,
    ESP32BridgeConfig,
    InjectedFault,
    PassengerWeightConfig,
    SensorSettings,
    SimulationSettings,
    TrafficPhase,
    TrafficProfile,
)

SUPPORTED_ALGORITHMS = ("MC_DDI", "SCAN", "FCFS")
SUPPORTED_DISTRIBUTIONS = ("POISSON", "UNIFORM")
SUPPORTED_TRANSPORTS = ("WIFI_TCP", "BLE_GATT")
SUPPORTED_FAULT_TYPES = ("VIBRATION_FAULT", "OUT_OF_SERVICE")
RATIO_SUM_TOLERANCE = 1e-6


class ConfigValidationError(ValueError):
    """Raised when the simulation JSON violates the documented schema."""


# --------------------------------------------------------------------------------------------
# Primitive field readers
# --------------------------------------------------------------------------------------------


def _section(obj: Dict[str, Any], key: str, path: str) -> Dict[str, Any]:
    if key not in obj:
        raise ConfigValidationError(f"Missing required section '{path}{key}'")
    val = obj[key]
    if not isinstance(val, dict):
        raise ConfigValidationError(f"'{path}{key}' must be an object")
    return val


def _require(obj: Dict[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise ConfigValidationError(f"Missing required field '{path}{key}'")
    return obj[key]


def _num(obj: Dict[str, Any], key: str, path: str) -> float:
    val = _require(obj, key, path)
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        raise ConfigValidationError(f"'{path}{key}' must be a finite number, got {val!r}")
    return float(val)


def _int(obj: Dict[str, Any], key: str, path: str) -> int:
    val = _require(obj, key, path)
    if isinstance(val, bool):
        raise ConfigValidationError(f"'{path}{key}' must be an integer, got {val!r}")
    if isinstance(val, float) and val.is_integer():
        return int(val)
    if not isinstance(val, int):
        raise ConfigValidationError(f"'{path}{key}' must be an integer, got {val!r}")
    return val


def _bool(obj: Dict[str, Any], key: str, path: str) -> bool:
    val = _require(obj, key, path)
    if not isinstance(val, bool):
        raise ConfigValidationError(f"'{path}{key}' must be true/false, got {val!r}")
    return val


def _str(obj: Dict[str, Any], key: str, path: str) -> str:
    val = _require(obj, key, path)
    if not isinstance(val, str) or not val.strip():
        raise ConfigValidationError(f"'{path}{key}' must be a non-empty string, got {val!r}")
    return val


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigValidationError(message)


# --------------------------------------------------------------------------------------------
# Section parsers
# --------------------------------------------------------------------------------------------


def _parse_simulation(raw: Dict[str, Any]) -> SimulationSettings:
    p = "simulation."
    name = _str(raw, "name", p)
    duration = _num(raw, "duration_sec", p)
    _check(duration > 0, f"{p}duration_sec must be > 0 (got {duration})")
    dt = _num(raw, "time_step_dt_sec", p)
    _check(0.05 <= dt <= 0.5, f"{p}time_step_dt_sec must be in [0.05, 0.5] (got {dt})")
    seed = _int(raw, "random_seed", p)
    scale = _num(raw, "realtime_scale", p)
    _check(scale >= 0.0, f"{p}realtime_scale must be >= 0.0 (got {scale})")
    return SimulationSettings(name, duration, dt, seed, scale)


def _parse_elevator(raw: Any, idx: int, min_floor: int, max_floor: int) -> ElevatorConfig:
    p = f"elevators[{idx}]."
    _check(isinstance(raw, dict), f"elevators[{idx}] must be an object")
    eid = _int(raw, "elevator_id", p)
    _check(eid >= 0, f"{p}elevator_id must be >= 0 (got {eid})")
    initial = _int(raw, "initial_floor", p)
    _check(
        min_floor <= initial <= max_floor,
        f"{p}initial_floor {initial} outside [{min_floor}, {max_floor}]",
    )
    speed = _num(raw, "max_speed_floors_per_sec", p)
    _check(speed > 0, f"{p}max_speed_floors_per_sec must be > 0 (got {speed})")
    times = {}
    for key in (
        "start_time_sec",
        "stop_time_sec",
        "door_open_time_sec",
        "door_close_time_sec",
        "dwell_time_sec",
    ):
        times[key] = _num(raw, key, p)
        _check(times[key] >= 0, f"{p}{key} must be >= 0 (got {times[key]})")
    cap = _int(raw, "max_capacity_passengers", p)
    _check(cap > 0, f"{p}max_capacity_passengers must be > 0 (got {cap})")
    wmax = _num(raw, "max_weight_kg", p)
    _check(wmax > 0, f"{p}max_weight_kg must be > 0 (got {wmax})")
    return ElevatorConfig(
        elevator_id=eid,
        max_speed=speed,
        start_time=times["start_time_sec"],
        stop_time=times["stop_time_sec"],
        door_open_time=times["door_open_time_sec"],
        door_close_time=times["door_close_time_sec"],
        dwell_time=times["dwell_time_sec"],
        max_weight_kg=wmax,
        max_capacity=cap,
        initial_floor=initial,
    )


def _parse_passengers(raw: Dict[str, Any]) -> tuple[float, PassengerWeightConfig]:
    p = "passengers."
    avg = _num(raw, "avg_weight_kg", p)
    std = _num(raw, "weight_std_kg", p)
    wmin = _num(raw, "min_weight_kg", p)
    wmax = _num(raw, "max_weight_kg", p)
    _check(avg > 0, f"{p}avg_weight_kg must be > 0 (got {avg})")
    _check(std >= 0, f"{p}weight_std_kg must be >= 0 (got {std})")
    _check(wmin > 0, f"{p}min_weight_kg must be > 0 (got {wmin})")
    _check(wmax > wmin, f"{p}max_weight_kg ({wmax}) must be > min_weight_kg ({wmin})")
    _check(
        wmin <= avg <= wmax,
        f"{p}avg_weight_kg ({avg}) must lie within [min_weight_kg, max_weight_kg] = [{wmin}, {wmax}]",
    )
    return avg, PassengerWeightConfig(std, wmin, wmax)


def _parse_dispatcher(raw: Dict[str, Any]) -> tuple[float, DispatcherSettings]:
    p = "dispatcher."
    algo = _str(raw, "algorithm", p)
    _check(algo in SUPPORTED_ALGORITHMS, f"{p}algorithm must be one of {SUPPORTED_ALGORITHMS} (got {algo!r})")
    buf = _num(raw, "weight_buffer_ratio", p)
    _check(0.0 <= buf <= 0.3, f"{p}weight_buffer_ratio must be in [0.0, 0.3] (got {buf})")
    soft = _num(raw, "soft_max_wait_sec", p)
    _check(soft > 0, f"{p}soft_max_wait_sec must be > 0 (got {soft})")
    fair = _num(raw, "fairness_weight", p)
    _check(fair >= 0, f"{p}fairness_weight must be >= 0 (got {fair})")
    parking = _bool(raw, "enable_idle_parking", p)
    timeout = _num(raw, "idle_parking_timeout_sec", p)
    _check(timeout > 0, f"{p}idle_parking_timeout_sec must be > 0 (got {timeout})")
    return buf, DispatcherSettings(algo, soft, fair, parking, timeout)


def _parse_sensors(raw: Dict[str, Any], elevator_ids: set[int]) -> SensorSettings:
    p = "sensors."
    vib = _num(raw, "max_vibration_threshold", p)
    _check(vib > 0, f"{p}max_vibration_threshold must be > 0 (got {vib})")
    prob = _num(raw, "door_obstruction_probability", p)
    _check(0.0 <= prob <= 1.0, f"{p}door_obstruction_probability must be in [0, 1] (got {prob})")
    delay = _num(raw, "door_obstruction_delay_sec", p)
    _check(delay >= 0, f"{p}door_obstruction_delay_sec must be >= 0 (got {delay})")

    faults_raw = raw.get("injected_faults", [])
    if faults_raw is None:
        faults_raw = []
    _check(isinstance(faults_raw, list), f"{p}injected_faults must be an array")
    faults: List[InjectedFault] = []
    for i, f in enumerate(faults_raw):
        fp = f"{p}injected_faults[{i}]."
        _check(isinstance(f, dict), f"{p}injected_faults[{i}] must be an object")
        eid = _int(f, "elevator_id", fp)
        _check(eid in elevator_ids, f"{fp}elevator_id {eid} does not match any configured elevator")
        ftype = _str(f, "type", fp)
        _check(ftype in SUPPORTED_FAULT_TYPES, f"{fp}type must be one of {SUPPORTED_FAULT_TYPES} (got {ftype!r})")
        start = _num(f, "start_time_sec", fp)
        end = _num(f, "end_time_sec", fp)
        _check(0 <= start < end, f"{fp} requires 0 <= start_time_sec < end_time_sec (got {start}, {end})")
        severity = _num(f, "severity", fp)
        _check(severity >= 0, f"{fp}severity must be >= 0 (got {severity})")
        faults.append(InjectedFault(eid, ftype, start, end, severity))
    return SensorSettings(vib, prob, delay, faults)


def _parse_traffic(raw: Dict[str, Any], duration: float) -> TrafficProfile:
    p = "traffic_generator."
    total_raw = raw.get("total_requests", None)
    if total_raw is None:
        total: Optional[int] = None
    else:
        total = _int(raw, "total_requests", p)
        _check(total >= 0, f"{p}total_requests must be > 0, 0 or null (got {total})")
    dist = _str(raw, "distribution", p)
    _check(dist in SUPPORTED_DISTRIBUTIONS, f"{p}distribution must be one of {SUPPORTED_DISTRIBUTIONS} (got {dist!r})")

    phases_raw = _require(raw, "phases", p)
    _check(isinstance(phases_raw, list) and len(phases_raw) > 0, f"{p}phases must be a non-empty array")
    phases: List[TrafficPhase] = []
    for i, ph in enumerate(phases_raw):
        pp = f"{p}phases[{i}]."
        _check(isinstance(ph, dict), f"{p}phases[{i}] must be an object")
        name = _str(ph, "name", pp)
        start = _num(ph, "start_time_sec", pp)
        end = _num(ph, "end_time_sec", pp)
        _check(
            0.0 <= start < end <= duration,
            f"{pp} requires 0.0 <= start_time_sec < end_time_sec <= duration_sec "
            f"(got {start}, {end}, duration {duration})",
        )
        rate = _num(ph, "arrival_rate_per_min", pp)
        _check(rate > 0, f"{pp}arrival_rate_per_min must be > 0 (got {rate})")
        ratios = []
        for key in ("up_peak_ratio", "down_peak_ratio", "inter_floor_ratio"):
            r = _num(ph, key, pp)
            _check(0.0 <= r <= 1.0, f"{pp}{key} must be in [0, 1] (got {r})")
            ratios.append(r)
        _check(
            abs(sum(ratios) - 1.0) < RATIO_SUM_TOLERANCE,
            f"{pp} up_peak_ratio + down_peak_ratio + inter_floor_ratio must equal 1.0 (got {sum(ratios)})",
        )
        phases.append(TrafficPhase(name, start, end, rate, *ratios))

    ordered = sorted(phases, key=lambda ph: ph.start_time_sec)
    for a, b in zip(ordered, ordered[1:]):
        _check(
            b.start_time_sec >= a.end_time_sec,
            f"{p}phases '{a.name}' [{a.start_time_sec}, {a.end_time_sec}] and "
            f"'{b.name}' [{b.start_time_sec}, {b.end_time_sec}] overlap",
        )
    return TrafficProfile(total, dist, ordered)


def _parse_esp32(raw: Optional[Dict[str, Any]]) -> ESP32BridgeConfig:
    if raw is None:
        return ESP32BridgeConfig()
    p = "esp32_bridge."
    _check(isinstance(raw, dict), "'esp32_bridge' must be an object")
    enabled = _bool(raw, "enabled", p)
    transport = _str(raw, "transport", p)
    _check(transport in SUPPORTED_TRANSPORTS, f"{p}transport must be one of {SUPPORTED_TRANSPORTS} (got {transport!r})")
    host = _str(raw, "host", p)
    port = _int(raw, "port", p)
    _check(1024 <= port <= 65535, f"{p}port must be in [1024, 65535] (got {port})")
    ble_uuid = raw.get("ble_service_uuid", "")
    if ble_uuid or transport == "BLE_GATT":
        ble_uuid = _str(raw, "ble_service_uuid", p)
        try:
            uuid.UUID(ble_uuid)
        except ValueError as exc:
            raise ConfigValidationError(f"{p}ble_service_uuid is not a valid UUID ({ble_uuid!r})") from exc
    timeout = _int(raw, "timeout_ms", p)
    _check(timeout > 0, f"{p}timeout_ms must be > 0 (got {timeout})")
    return ESP32BridgeConfig(enabled, transport, host, port, ble_uuid, timeout)


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


def parse_config(raw: Dict[str, Any]) -> BuildingConfig:
    """Validate a decoded JSON object and build a :class:`BuildingConfig`."""
    _check(isinstance(raw, dict), "Top-level config must be a JSON object")
    schema_version = raw.get("schema_version", "1.0")
    _check(schema_version == "1.0", f"Unsupported schema_version {schema_version!r} (expected '1.0')")

    simulation = _parse_simulation(_section(raw, "simulation", ""))

    b = _section(raw, "building", "")
    min_floor = _int(b, "min_floor", "building.")
    max_floor = _int(b, "max_floor", "building.")
    _check(min_floor < max_floor, f"building.min_floor ({min_floor}) must be < max_floor ({max_floor})")
    lobby = _int(b, "lobby_floor", "building.")
    _check(min_floor <= lobby <= max_floor, f"building.lobby_floor {lobby} outside [{min_floor}, {max_floor}]")
    height = _num(b, "floor_height_meters", "building.")
    _check(height > 0, f"building.floor_height_meters must be > 0 (got {height})")

    elevators_raw = _require(raw, "elevators", "")
    _check(isinstance(elevators_raw, list) and len(elevators_raw) > 0, "'elevators' must be a non-empty array")
    elevators = [_parse_elevator(e, i, min_floor, max_floor) for i, e in enumerate(elevators_raw)]
    ids = [e.elevator_id for e in elevators]
    _check(len(set(ids)) == len(ids), f"elevators[].elevator_id values must be unique (got {ids})")

    avg_weight, passengers = _parse_passengers(_section(raw, "passengers", ""))
    buffer_ratio, dispatcher = _parse_dispatcher(_section(raw, "dispatcher", ""))
    sensors = _parse_sensors(_section(raw, "sensors", ""), set(ids))
    traffic_raw = _section(raw, "traffic_generator", "")
    traffic = _parse_traffic(traffic_raw, simulation.duration_sec)
    esp32 = _parse_esp32(raw.get("esp32_bridge"))

    return BuildingConfig(
        min_floor=min_floor,
        max_floor=max_floor,
        elevators=elevators,
        avg_passenger_weight_kg=avg_weight,
        weight_buffer_ratio=buffer_ratio,
        traffic_profile=copy.deepcopy(traffic_raw),
        lobby_floor=lobby,
        floor_height_meters=height,
        simulation=simulation,
        passengers=passengers,
        dispatcher=dispatcher,
        sensors=sensors,
        traffic=traffic,
        esp32_bridge=esp32,
        schema_version=schema_version,
    )


def load_config(path: Union[str, Path]) -> BuildingConfig:
    """Read, decode and validate ``simulation_config.json`` from disk."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigValidationError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"Config file {path} is not valid JSON: {exc}") from exc
    return parse_config(raw)
