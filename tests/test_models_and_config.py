"""Phase 2 — DTOs and the fail-fast JSON config loader (simulation_configuration_documentation.md)."""
from __future__ import annotations

import dataclasses
import json

import pytest

from src.config_loader import ConfigValidationError, load_config, parse_config
from src.models import (
    AllocationDecision,
    Direction,
    ElevatorAssignment,
    ElevatorState,
    Request,
    RequestStatus,
    SensorSnapshot,
    StopRecord,
)

# ============================================================ Parsing the project config


def test_project_config_parses(building_config):
    c = building_config
    assert (c.min_floor, c.max_floor, c.lobby_floor) == (-2, 15, 0)
    assert c.floors == list(range(-2, 16))
    assert c.avg_passenger_weight_kg == 75.0
    assert c.weight_buffer_ratio == 0.10
    assert c.simulation.name == "Office_Building_Morning_Rush"
    assert c.simulation.time_step_dt_sec == 0.1
    assert c.simulation.random_seed == 42
    assert c.dispatcher.algorithm == "MC_DDI"
    assert c.dispatcher.soft_max_wait_sec == 60.0
    assert c.traffic.total_requests == 200
    assert [p.name for p in c.traffic.phases] == ["morning_up_peak", "midday_mixed", "evening_down_peak"]
    assert c.traffic_profile["distribution"] == "POISSON"  # LLD raw dict preserved
    assert c.esp32_bridge.enabled is False and c.esp32_bridge.port == 5000


def test_elevator_configs_mapped(building_config):
    e2 = building_config.get_elevator_config(2)
    assert e2.max_speed == 2.5 and e2.start_time == 1.2 and e2.stop_time == 1.2
    assert e2.max_capacity == 10 and e2.max_weight_kg == 800.0 and e2.initial_floor == 8
    assert e2.door_cycle_time == pytest.approx(7.0)
    with pytest.raises(KeyError):
        building_config.get_elevator_config(99)


def test_injected_fault_window(building_config):
    (fault,) = building_config.sensors.injected_faults
    assert (fault.elevator_id, fault.type, fault.severity) == (1, "VIBRATION_FAULT", 7.5)
    assert not fault.is_active(1199.9)
    assert fault.is_active(1200.0) and fault.is_active(1319.9)
    assert not fault.is_active(1320.0)
    assert fault.severity > building_config.sensors.max_vibration_threshold


def test_load_config_file_errors(tmp_path):
    with pytest.raises(ConfigValidationError, match="not found"):
        load_config(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ConfigValidationError, match="not valid JSON"):
        load_config(bad)


def test_optional_sections_default(make_config):
    def mut(raw):
        del raw["esp32_bridge"]
        del raw["sensors"]["injected_faults"]

    cfg = make_config(mut)
    assert cfg.esp32_bridge.enabled is False
    assert cfg.sensors.injected_faults == []


def test_config_is_immutable(building_config):
    with pytest.raises(dataclasses.FrozenInstanceError):
        building_config.min_floor = 3  # type: ignore[misc]


# ============================================================ Fail-fast validation


def _set(path, value):
    def mut(raw):
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mut


def _delete(path):
    def mut(raw):
        node = raw
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mut


@pytest.mark.parametrize(
    "mutator, match",
    [
        (_delete(["building"]), "Missing required section 'building'"),
        (_set(["schema_version"], "2.0"), "schema_version"),
        (_set(["simulation", "name"], ""), "simulation.name"),
        (_set(["simulation", "duration_sec"], 0), "duration_sec must be > 0"),
        (_set(["simulation", "time_step_dt_sec"], 1.0), r"\[0.05, 0.5\]"),
        (_set(["simulation", "random_seed"], 4.5), "random_seed' must be an integer"),
        (_set(["simulation", "realtime_scale"], -1.0), "realtime_scale"),
        (_set(["building", "min_floor"], 15), "min_floor"),
        (_set(["building", "lobby_floor"], 20), "lobby_floor"),
        (_set(["building", "floor_height_meters"], 0), "floor_height_meters"),
        (_set(["elevators"], []), "non-empty"),
        (_set(["elevators", 1, "elevator_id"], 0), "unique"),
        (_set(["elevators", 0, "elevator_id"], -1), "elevator_id must be >= 0"),
        (_set(["elevators", 0, "initial_floor"], 99), "initial_floor"),
        (_set(["elevators", 0, "max_speed_floors_per_sec"], 0), "max_speed"),
        (_set(["elevators", 0, "dwell_time_sec"], -0.1), "dwell_time_sec"),
        (_set(["elevators", 0, "max_capacity_passengers"], 0), "max_capacity_passengers"),
        (_set(["elevators", 0, "max_weight_kg"], "heavy"), "finite number"),
        (_set(["elevators", 0, "max_weight_kg"], True), "finite number"),
        (_set(["passengers", "avg_weight_kg"], 200.0), "avg_weight_kg"),
        (_set(["passengers", "max_weight_kg"], 30.0), "max_weight_kg"),
        (_set(["passengers", "weight_std_kg"], -1.0), "weight_std_kg"),
        (_set(["dispatcher", "algorithm"], "GREEDY"), "algorithm"),
        (_set(["dispatcher", "weight_buffer_ratio"], 0.5), "weight_buffer_ratio"),
        (_set(["dispatcher", "soft_max_wait_sec"], 0), "soft_max_wait_sec"),
        (_set(["dispatcher", "fairness_weight"], -0.1), "fairness_weight"),
        (_set(["dispatcher", "enable_idle_parking"], "yes"), "true/false"),
        (_set(["dispatcher", "idle_parking_timeout_sec"], 0), "idle_parking_timeout_sec"),
        (_set(["sensors", "max_vibration_threshold"], 0), "max_vibration_threshold"),
        (_set(["sensors", "door_obstruction_probability"], 1.5), "door_obstruction_probability"),
        (_set(["sensors", "door_obstruction_delay_sec"], -1), "door_obstruction_delay_sec"),
        (_set(["sensors", "injected_faults", 0, "elevator_id"], 7), "does not match"),
        (_set(["sensors", "injected_faults", 0, "type"], "FIRE"), "type must be one of"),
        (_set(["sensors", "injected_faults", 0, "end_time_sec"], 1000.0), "start_time_sec < end_time_sec"),
        (_set(["traffic_generator", "total_requests"], -5), "total_requests"),
        (_set(["traffic_generator", "distribution"], "GAUSSIAN"), "distribution"),
        (_set(["traffic_generator", "phases"], []), "non-empty"),
        (_set(["traffic_generator", "phases", 0, "arrival_rate_per_min"], 0), "arrival_rate_per_min"),
        (_set(["traffic_generator", "phases", 0, "up_peak_ratio"], 0.80), "must equal 1.0"),
        (_set(["traffic_generator", "phases", 2, "end_time_sec"], 4000.0), "duration_sec"),
        (_set(["traffic_generator", "phases", 0, "start_time_sec"], 1300.0), "start_time_sec < end_time_sec"),
        (_set(["traffic_generator", "phases", 1, "start_time_sec"], 1100.0), "overlap"),
        (_set(["esp32_bridge", "transport"], "ZIGBEE"), "transport"),
        (_set(["esp32_bridge", "port"], 80), "port"),
        (_set(["esp32_bridge", "ble_service_uuid"], "not-a-uuid"), "UUID"),
        (_set(["esp32_bridge", "timeout_ms"], 0), "timeout_ms"),
    ],
)
def test_invalid_config_fails_fast(raw_config_copy, mutator, match):
    mutator(raw_config_copy)
    with pytest.raises(ConfigValidationError, match=match):
        parse_config(raw_config_copy)


def test_ratio_sum_tolerance(raw_config_copy):
    ph = raw_config_copy["traffic_generator"]["phases"][0]
    ph.update(up_peak_ratio=0.7, down_peak_ratio=0.2, inter_floor_ratio=0.1)  # 0.99999999… in float
    parse_config(raw_config_copy)


def test_integral_floats_accepted_for_int_fields(raw_config_copy):
    raw_config_copy["esp32_bridge"]["port"] = 5000.0
    assert parse_config(raw_config_copy).esp32_bridge.port == 5000


def test_out_of_order_phases_are_sorted(raw_config_copy):
    raw_config_copy["traffic_generator"]["phases"].reverse()
    cfg = parse_config(raw_config_copy)
    assert [p.start_time_sec for p in cfg.traffic.phases] == [0.0, 1200.0, 2400.0]


# ============================================================ DTOs and enums


def test_request_defaults_and_derived_times():
    r = Request(req_id=3, timestamp=10.0, src_floor=0, target_floor=5, actual_weight_kg=80.0)
    assert r.status is RequestStatus.PENDING
    assert r.assigned_elevator_id is None and r.wait_time is None and r.journey_time is None
    r.board_time, r.arrival_time = 25.0, 40.0
    assert r.wait_time == 15.0 and r.journey_time == 30.0


def test_sensor_snapshot_is_immutable_and_signed_speed():
    s = SensorSnapshot(0, 1.0, 3.5, -2.0, 150.0, 0.2, False, True, False)
    assert s.speed < 0  # moving down
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.position = 4.0  # type: ignore[misc]


def test_other_dtos_have_independent_defaults():
    a, b = StopRecord(1.0, 0, 3), StopRecord(2.0, 1, 4)
    a.boarded_req_ids.append(7)
    assert b.boarded_req_ids == []
    ea, eb = ElevatorAssignment(), ElevatorAssignment()
    ea.stop_queue.append(5)
    assert eb.stop_queue == []
    d = AllocationDecision(req_id=1, elevator_id=2, planned_route=[0, 8], estimated_cost=12.5)
    assert d.compute_time_ms == 0.0


def test_enums_match_lld_and_serialize_as_strings():
    assert [d.value for d in Direction] == ["UP", "DOWN", "IDLE"]
    assert [s.value for s in ElevatorState] == [
        "IDLE", "ACCELERATING", "CRUISING", "DECELERATING", "DOOR_OPENING",
        "DWELLING", "DOOR_CLOSING", "OVERLOADED", "OUT_OF_SERVICE",
    ]
    assert [s.value for s in RequestStatus] == ["PENDING", "ASSIGNED", "BOARDED", "COMPLETED", "BUMPED_OVERLOAD"]
    assert json.dumps({"s": RequestStatus.COMPLETED}) == '{"s": "COMPLETED"}'
