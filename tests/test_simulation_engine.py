"""Phase 3 — SensorSuite, Elevator physics, Building, SimulationEngine and SimulationLogger.

Covers testing_strategy_markdown.md Layer 1 (kinematics, braking floor, sensor interlocks),
Layer 2 (conservation/safety invariants, overload recovery, vibration failover, door obstruction)
and Layer 5 (simulation_output.csv / simulation_summary.json validation).
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys

import pytest

from src.dispatchers.base import IDispatcher
from src.models.dto import AllocationDecision, Request
from src.models.enums import Direction, ElevatorState, RequestStatus
from src.request_generator import RequestGenerator
from src.simulation import (
    OUTPUT_HEADER,
    SUMMARY_KEYS,
    Building,
    DispatchError,
    SensorSuite,
    SimulationEngine,
    SimulationLogger,
)
from tests.conftest import ROOT
from tests.helpers import RecordingObserver, TailAppendDispatcher

# ============================================================================ helpers


def single_car(raw, **overrides):
    """Mutator: keep only elevator 0, no faults, no random door obstructions, no parking."""
    raw["elevators"] = [raw["elevators"][0]]
    raw["elevators"][0].update(overrides)
    raw["sensors"]["injected_faults"] = []
    raw["sensors"]["door_obstruction_probability"] = 0.0
    raw["dispatcher"]["enable_idle_parking"] = False


def deterministic_fleet(raw):
    raw["sensors"]["injected_faults"] = []
    raw["sensors"]["door_obstruction_probability"] = 0.0
    raw["dispatcher"]["enable_idle_parking"] = False


def req(i, t, s, d, w=75.0):
    return Request(req_id=i, timestamp=t, src_floor=s, target_floor=d, actual_weight_kg=w)


def run(config, requests, dispatcher=None, setup=None):
    engine = SimulationEngine(config, dispatcher or TailAppendDispatcher(), requests)
    obs = RecordingObserver(engine.building)
    logger = SimulationLogger(output_dir=None)
    engine.register_observer(obs)
    engine.register_observer(logger)
    if setup:
        setup(engine, obs)
    summary = engine.run()
    return engine, obs, logger, summary


def stop_times(obs, elevator_id=None):
    return [(round(s.timestamp, 6), s.floor) for s in obs.stops if elevator_id is None or s.elevator_id == elevator_id]


def parse_output(text):
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture(scope="module")
def full_run(building_config):
    """Project config + generated 200-request stream through the stub dispatcher (with real faults and
    stochastic door obstructions)."""
    requests = RequestGenerator().generate(building_config)

    def keep(engine, obs):
        obs.keep_snapshots = True

    engine, obs, logger, summary = run(building_config, requests, setup=keep)
    return requests, engine, obs, logger, summary


# ============================================================================ SensorSuite


def test_sensor_overload_threshold_is_strict():
    s = SensorSuite(max_weight_kg=640.0, vibration_threshold=5.0)
    assert not s.is_overloaded(640.0)
    assert s.is_overloaded(640.1)


def test_sensor_safety_interlocks():
    s = SensorSuite(640.0, 5.0)
    assert s.is_safe_to_operate()
    s.inject_vibration_fault(5.0)
    assert s.is_safe_to_operate()  # threshold is inclusive
    s.inject_vibration_fault(7.5)
    assert not s.is_safe_to_operate()
    s.inject_vibration_fault(0.0)
    s.set_fault_active(True)
    assert not s.is_safe_to_operate()


def test_sensor_snapshot_contents():
    s = SensorSuite(640.0, 5.0)
    s.inject_door_obstruction(True)
    s.inject_vibration_fault(6.0)
    snap = s.read_snapshot(2, 12.5, 3.4, -2.0, 300.0, True)
    assert (snap.elevator_id, snap.timestamp, snap.position, snap.speed) == (2, 12.5, 3.4, -2.0)
    assert snap.current_weight_kg == 300.0 and snap.vibration_level == 6.0
    assert snap.door_obstructed and snap.is_at_capacity and not snap.is_operational


# ============================================================================ kinematics


def test_single_trip_timing_matches_leg_formula(make_config):
    cfg = make_config(single_car)
    e, obs, _, summary = run(cfg, [req(0, 0.0, 0, 10)])
    # STOP at 0 immediately; doors 2 + dwell 3 + close 2 = 7 s; leg 1.5 + 10/2.0 + 1.5 = 8 s
    assert stop_times(obs) == [(0.0, 0), (15.0, 10)]
    r = e.requests[0]
    assert r.board_time == 0.0 and r.arrival_time == pytest.approx(15.0)
    assert summary["avg_journey_time_sec"] == 15.0 and summary["avg_hall_wait_time_sec"] == 0.0


def test_heterogeneous_car_timing(make_config):
    def mut(raw):
        single_car(raw)
        raw["elevators"][0] = dict(raw["elevators"][0], max_speed_floors_per_sec=2.5, start_time_sec=1.2,
                                   stop_time_sec=1.2, initial_floor=8)

    e, obs, _, _ = run(make_config(mut), [req(0, 0.0, 3, -2)])
    # 8 -> 3: 1.2 + 5/2.5 + 1.2 = 4.4 ; doors 7 ; 3 -> -2: 4.4
    assert stop_times(obs) == [(4.4, 3), (15.8, -2)]


def test_call_dispatched_at_exact_non_tick_timestamp(make_config):
    e, obs, logger, _ = run(make_config(single_car), [req(0, 4.12, 0, 11)])
    assert stop_times(obs)[0] == (4.12, 0)
    rows = parse_output(logger.to_csv_text())
    assert rows[0] == {"time": "4.12", "src": "0", "target": "11", "elevator_id": "0", "event_type": "ALLOC"}
    assert rows[1] == {"time": "4.12", "src": "0", "target": "", "elevator_id": "0", "event_type": "STOP"}


def test_dt_does_not_change_event_times(make_config):
    reqs = [req(0, 0.0, 0, 10), req(1, 3.33, 4, 1), req(2, 20.07, 7, 15)]
    results = []
    for dt in (0.05, 0.1, 0.37):
        def mut(raw, dt=dt):
            single_car(raw)
            raw["simulation"]["time_step_dt_sec"] = dt

        _, obs, _, _ = run(make_config(mut), reqs)
        results.append(stop_times(obs))
    assert results[0] == results[1] == results[2]


def _moving_elevator(make_config):
    """Single car at floor 0 serving 0->10; returns (building, elevator) frozen at t=10.2, position 3.4."""
    cfg = make_config(single_car)
    b = Building(cfg)
    e = b.get_elevator(0)
    e.assign_request(req(0, 0.0, 0, 10), [0, 10])
    t = 0.0
    while t < 10.2 - 1e-9:  # stop@0 (0..7 s doors) -> accelerate 7..8.5 -> cruise from 8.5
        e.step(0.1, t)
        t = round(t + 0.1, 9)
    assert [r.floor for r in e.drain_stop_records()] == [0]
    return b, e, t


def test_braking_floor_while_cruising_up(make_config):
    _, e, _ = _moving_elevator(make_config)
    assert e.state == ElevatorState.CRUISING and e.direction == Direction.UP
    assert e.position == pytest.approx(3.4)
    assert e.get_braking_floor() == 4
    assert e.get_telemetry(10.2).speed == pytest.approx(2.0)


def test_braking_floor_other_states(make_config):
    cfg = make_config(single_car)
    e = Building(cfg).get_elevator(0)
    assert e.get_braking_floor() == 0  # idle
    e.assign_request(req(0, 0.0, 0, -2), [0, -2])
    t = 0.0
    while e.state != ElevatorState.ACCELERATING:
        e.step(0.1, t)
        t = round(t + 0.1, 9)
    assert e.get_braking_floor() == -1  # cannot stop at the floor it is leaving
    while e.state != ElevatorState.CRUISING:
        e.step(0.1, t)
        t = round(t + 0.1, 9)
    e.step(0.3, t)  # 0.6 floors down -> position -0.6
    assert e.position == pytest.approx(-0.6) and e.get_braking_floor() == -1
    while e.state != ElevatorState.DECELERATING:
        e.step(0.1, t)
        t = round(t + 0.1, 9)
    assert e.get_braking_floor() == -2


def test_retarget_to_reachable_floor_ahead(make_config):
    _, e, t = _moving_elevator(make_config)
    e.assign_request(req(1, t, 6, 10), [6, 10])
    while not e.drain_stop_records() and t < 30:
        e.step(0.1, t)
        t = round(t + 0.1, 9)
    # cruise 0 -> 6 takes 3 s from 8.5 -> arrive 11.5, decelerate 1.5 -> STOP at 13.0
    assert e.current_floor == 6 and e._current_stop.timestamp == pytest.approx(13.0)


def test_point_of_no_return_turnaround(make_config):
    """A stop behind the braking floor forces a door-less stop at the braking floor, then a reversal."""
    _, e, t = _moving_elevator(make_config)
    e.assign_request(req(1, t, 2, 10), [2, 10])
    records = []
    while not records and t < 40:
        e.step(0.1, t)
        records = e.drain_stop_records()
        t = round(t + 0.1, 9)
    # brake at 4: arrive 10.5, decel to 12.0 (no doors); leg 4 -> 2 = 1.5 + 1 + 1.5 -> STOP at 16.0
    assert [(r.floor, round(r.timestamp, 6)) for r in records] == [(2, 16.0)]


# ============================================================================ door cycle & obstruction


def test_late_boarder_merges_into_open_door_stop(make_config):
    e, obs, _, _ = run(make_config(single_car), [req(0, 0.0, 0, 5), req(1, 3.0, 0, 7)])
    # one stop at floor 0 (not two); legs 0->5 = 5.5 s, 5->7 = 4.0 s, doors 7 s
    assert stop_times(obs) == [(0.0, 0), (12.5, 5), (23.5, 7)]
    assert e.requests[1].board_time == 3.0


def test_deterministic_door_obstruction_shifts_later_stops_by_exactly_delay(make_config):
    cfg = make_config(single_car)
    reqs = [req(0, 0.0, 0, 5), req(1, 0.0, 0, 9)]
    _, base, _, _ = run(cfg, reqs)

    def obstruct(engine, obs):
        engine.building.get_elevator(0).force_next_door_obstruction()
        obs.keep_snapshots = True

    _, delayed, _, _ = run(cfg, reqs, setup=obstruct)
    b, d = stop_times(base), stop_times(delayed)
    assert b[0] == d[0] == (0.0, 0)
    delay = cfg.sensors.door_obstruction_delay_sec
    assert delay == 4.0
    for (tb, fb), (td, fd) in zip(b[1:], d[1:]):
        assert fb == fd and td - tb == pytest.approx(delay, abs=1e-6)
    flagged = [t for t, snaps in delayed.snapshots_by_tick if snaps[0].door_obstructed]
    assert flagged and min(flagged) > 5.0 - 1e-9 and max(flagged) <= 9.0 + 1e-9


def test_stochastic_door_obstructions(make_config):
    def mut(raw):
        single_car(raw)
        raw["sensors"]["door_obstruction_probability"] = 1.0

    reqs = [req(0, 0.0, 0, 5), req(1, 0.0, 0, 9)]
    _, base, _, _ = run(make_config(single_car), reqs)
    _, always, _, _ = run(make_config(mut), reqs)
    # every closing cycle obstructed: 2nd stop +4 s, 3rd stop +8 s
    assert [t for t, _ in stop_times(always)] == pytest.approx([0.0, stop_times(base)[1][0] + 4,
                                                                 stop_times(base)[2][0] + 8])


# ============================================================================ overload recovery


def test_weight_overload_bumps_and_redispatches(make_config):
    cfg = make_config(deterministic_fleet)
    heavy = [req(i, 0.0, 0, 10, w=140.0) for i in range(6)]  # car 0: W_max 640 -> only 4 fit (560 kg)
    seen_capacity_lock = []

    def watch(engine, obs):
        car0 = engine.building.get_elevator(0)
        orig = obs.on_tick

        def on_tick(t, snaps):
            seen_capacity_lock.append((t, snaps[0].is_at_capacity, car0.state))
            orig(t, snaps)

        obs.on_tick = on_tick

    e, obs, logger, summary = run(cfg, heavy, TailAppendDispatcher(forced_elevator=0), setup=watch)

    assert summary["overload_events"] == 2
    assert [(eid, rid) for eid, rid, _ in obs.overloads] == [(0, 4), (0, 5)]
    # boarding (and the load-sensor trip) happens once the doors finish opening at t = 2.0
    assert all(t == pytest.approx(cfg.elevators[0].door_open_time, abs=cfg.simulation.time_step_dt_sec) for _, _, t in obs.overloads)
    reallocs = [a for a in obs.allocations[6:]]
    assert [rid for rid, _, _ in reallocs] == [4, 5]
    assert all(eid != 0 for _, eid, _ in reallocs)

    by_id = {r.req_id: r for r in e.requests}
    for rid in (4, 5):
        r = by_id[rid]
        assert r.status == RequestStatus.COMPLETED
        assert r.assigned_elevator_id != 0
        assert r.timestamp == 0.0  # original call time retained
        assert r.journey_time == pytest.approx(r.arrival_time - 0.0)
    assert sorted(r.req_id for r in e.requests if r.assigned_elevator_id == 0) == [0, 1, 2, 3]

    # capacity lock + OVERLOADED door hold, released after the drop-off at floor 10
    assert any(lock and st == ElevatorState.OVERLOADED for _, lock, st in seen_capacity_lock)
    lock_times = [t for t, lock, _ in seen_capacity_lock if lock]
    assert max(lock_times) < by_id[0].arrival_time + cfg.elevators[0].door_open_time + 1e-6
    assert obs.safety_violations == []

    rows = parse_output(logger.to_csv_text())
    assert [r["event_type"] for r in rows].count("REALLOC") == 2
    assert [r["event_type"] for r in rows].count("ALLOC") == 6


def test_headcount_overload(make_config):
    cfg = make_config(deterministic_fleet)
    light = [req(i, 0.0, 0, 3, w=40.0) for i in range(9)]  # car 0 holds 8 passengers
    e, obs, _, summary = run(cfg, light, TailAppendDispatcher(forced_elevator=0))
    assert summary["overload_events"] == 1
    assert obs.overloads[0][:2] == (0, 8)
    assert summary["completed_requests"] == 9
    assert obs.safety_violations == []


def test_capacity_locked_car_does_not_board_until_drop_off(make_config):
    """If a dispatcher still sends a pickup to a capacity-locked car, it is not boarded (and the
    passenger is still delivered later)."""
    cfg = make_config(deterministic_fleet)

    class StubbornDispatcher(TailAppendDispatcher):
        def allocate(self, request, fleet_snapshots, active_assignments):
            route = list(active_assignments[0].stop_queue)
            if not route or route[-1] != request.src_floor:
                route.append(request.src_floor)
            route.append(request.target_floor)
            return AllocationDecision(request.req_id, 0, route, 0.0)

    reqs = [req(i, 0.0, 0, 10, w=140.0) for i in range(5)]
    e, obs, _, summary = run(cfg, reqs, StubbornDispatcher())
    assert summary["completed_requests"] == 5
    assert summary["overload_events"] >= 1
    assert obs.safety_violations == []


def test_safety_interlock_refuses_overweight_departure(make_config):
    from src.simulation.elevator import SafetyInterlockError

    cfg = make_config(single_car)
    e = Building(cfg).get_elevator(0)
    e.onboard_requests = [req(i, 0.0, 0, 5, w=200.0) for i in range(4)]  # 800 kg forced in
    e.stop_queue = [5]
    with pytest.raises(SafetyInterlockError):
        e.step(0.1, 0.0)


# ============================================================================ vibration fault failover


def test_vibration_fault_failover(full_run, building_config):
    _, engine, obs, _, summary = full_run
    fault = building_config.sensors.injected_faults[0]
    during = [(t, eid) for t, _, eid, _ in engine.allocation_log if fault.start_time_sec <= t < fault.end_time_sec]
    after = [(t, eid) for t, _, eid, _ in engine.allocation_log if t >= fault.end_time_sec]
    assert during, "expected allocations during the fault window"
    assert all(eid != 1 for _, eid in during)
    assert any(eid == 1 for _, eid in after), "car 1 should resume receiving allocations after 1320 s"

    for t, snaps in obs.snapshots_by_tick:
        s1 = next(s for s in snaps if s.elevator_id == 1)
        in_window = fault.start_time_sec <= t < fault.end_time_sec
        assert s1.is_operational is (not in_window)
        assert s1.vibration_level == (fault.severity if in_window else 0.0)
    assert summary["completed_requests"] == 200  # car 1 finished its existing passengers


def test_out_of_service_fault_type(make_config):
    def mut(raw):
        deterministic_fleet(raw)
        raw["sensors"]["injected_faults"] = [
            {"elevator_id": 0, "type": "OUT_OF_SERVICE", "start_time_sec": 0.0, "end_time_sec": 100.0, "severity": 0.0}
        ]

    e, obs, _, _ = run(make_config(mut), [req(i, float(i), 0, 5 + i) for i in range(6)])
    assert all(eid != 0 for _, eid, _ in obs.allocations)


# ============================================================================ global invariants on a full run


def test_full_run_conservation_and_safety(full_run):
    requests, engine, obs, _, summary = full_run
    assert not engine.stalled
    assert summary["total_requests"] == summary["completed_requests"] == len(requests) == 200
    for r in engine.requests:
        assert r.status == RequestStatus.COMPLETED
        assert r.arrival_time > r.board_time >= r.timestamp
    assert obs.safety_violations == []
    assert all(not b.floor_queues[f] for b in [engine.building] for f in b.floor_queues)


def test_full_run_is_deterministic(building_config, full_run):
    requests, _, _, logger_a, summary_a = full_run
    _, _, logger_b, summary_b = run(building_config, requests)
    assert logger_a.to_csv_text() == logger_b.to_csv_text()
    strip = lambda s: {k: v for k, v in s.items() if k != "avg_dispatch_latency_ms"}  # noqa: E731
    assert strip(summary_a) == strip(summary_b)


def test_engine_does_not_mutate_caller_requests(full_run):
    requests, *_ = full_run
    assert all(r.status == RequestStatus.PENDING and r.board_time is None for r in requests)


def test_3col_requests_run_with_default_weight(building_config, tmp_path):
    gen = RequestGenerator()
    path = tmp_path / "r.csv"
    gen.save_to_file(gen.generate(building_config)[:40], path, strict_3col=True)
    reqs = gen.load_from_file(path, default_weight_kg=building_config.avg_passenger_weight_kg)
    _, _, _, summary = run(building_config, reqs)
    assert summary["completed_requests"] == 40


# ============================================================================ Layer 5: output validation


def test_output_csv_schema_and_order(full_run):
    _, _, _, logger, _ = full_run
    text = logger.to_csv_text()
    assert text.splitlines()[0] == ",".join(OUTPUT_HEADER)
    rows = parse_output(text)
    times = [float(r["time"]) for r in rows]
    assert times == sorted(times)
    for r in rows:
        assert r["event_type"] in {"ALLOC", "REALLOC", "STOP"}
        int(r["src"]), int(r["elevator_id"])
        if r["event_type"] == "STOP":
            assert r["target"] == ""
        else:
            int(r["target"])


def test_output_alloc_rows_match_input_one_to_one(full_run):
    requests, _, _, logger, _ = full_run
    allocs = [(r["time"], r["src"], r["target"]) for r in parse_output(logger.to_csv_text()) if r["event_type"] == "ALLOC"]
    expected = [(f"{r.timestamp:.2f}", str(r.src_floor), str(r.target_floor)) for r in requests]
    assert sorted(allocs) == sorted(expected) and len(allocs) == len(expected)


def test_every_pickup_and_dropoff_has_a_stop_row(full_run):
    _, engine, _, logger, _ = full_run
    stops = {
        (r["time"], int(r["src"]), int(r["elevator_id"]))
        for r in parse_output(logger.to_csv_text())
        if r["event_type"] == "STOP"
    }
    for r in engine.requests:
        assert (f"{r.board_time:.2f}", r.src_floor, r.assigned_elevator_id) in stops or any(
            # late boarders walk in during an ongoing stop at their floor
            float(t) <= r.board_time and f == r.src_floor and e == r.assigned_elevator_id for t, f, e in stops
        )
        assert (f"{r.arrival_time:.2f}", r.target_floor, r.assigned_elevator_id) in stops


def test_summary_consistent_with_csv(full_run):
    _, engine, _, logger, summary = full_run
    rows = parse_output(logger.to_csv_text())
    assert list(summary.keys()) == SUMMARY_KEYS
    assert summary["total_elevator_stops"] == sum(r["event_type"] == "STOP" for r in rows)
    assert summary["overload_events"] == sum(r["event_type"] == "REALLOC" for r in rows)
    j = [r.journey_time for r in engine.requests]
    w = [r.wait_time for r in engine.requests]
    assert summary["total_journey_time_sec"] == round(sum(j), 2)
    assert summary["avg_journey_time_sec"] == round(sum(j) / 200, 2)
    assert summary["avg_hall_wait_time_sec"] == round(sum(w) / 200, 2)
    assert summary["max_hall_wait_time_sec"] == round(max(w), 2)
    assert summary["algorithm"] == "TEST_TAIL" and summary["execution_mode"] == "LOCAL"
    assert summary["simulation_name"] == "Office_Building_Morning_Rush"


def test_logger_exports_files(make_config, tmp_path):
    cfg = make_config(single_car)
    engine = SimulationEngine(cfg, TailAppendDispatcher(), [req(0, 0.0, 0, 10)])
    engine.register_observer(SimulationLogger(output_dir=tmp_path))
    engine.run()
    csv_text = (tmp_path / "simulation_output.csv").read_text()
    assert csv_text == "time,src,target,elevator_id,event_type\n0.00,0,10,0,ALLOC\n0.00,0,,0,STOP\n15.00,10,,0,STOP\n"
    summary = json.loads((tmp_path / "simulation_summary.json").read_text())
    assert list(summary) == SUMMARY_KEYS
    assert summary["total_elevator_stops"] == 2 and summary["avg_journey_time_sec"] == 15.0


# ============================================================================ engine API / robustness


def test_idle_parking_moves_without_stop_rows(make_config):
    def mut(raw):
        single_car(raw)
        raw["dispatcher"]["enable_idle_parking"] = True

    class ParkAt5(TailAppendDispatcher):
        def get_parking_targets(self, fleet_snapshots, active_assignments, current_time):
            return {0: 5}

    e, obs, _, _ = run(make_config(mut), [req(0, 100.0, 5, 6)], ParkAt5())
    assert stop_times(obs)[0] == (100.0, 5)  # already parked at floor 5 when the call arrives
    assert len(obs.stops) == 2


def test_step_once_and_observer_ticks(make_config):
    cfg = make_config(single_car)
    engine = SimulationEngine(cfg, TailAppendDispatcher(), [req(0, 0.0, 0, 2)])
    obs = RecordingObserver(engine.building)
    engine.register_observer(obs)
    assert engine.step_once() is True
    assert engine.current_time == pytest.approx(0.1) and obs.ticks == 1
    while engine.step_once():
        pass
    assert engine.is_finished() and obs.summary is None
    summary = engine.finalize()
    assert obs.summary == summary and engine.finalize() is summary


def test_empty_request_list_finishes_immediately(make_config):
    engine = SimulationEngine(make_config(single_car), TailAppendDispatcher(), [])
    assert engine.step_once() is False
    assert engine.run()["completed_requests"] == 0


def test_invalid_requests_rejected(make_config):
    cfg = make_config(single_car)
    with pytest.raises(ValueError, match="outside the building"):
        SimulationEngine(cfg, TailAppendDispatcher(), [req(0, 0.0, 0, 99)])


def test_bad_dispatcher_decision_raises(make_config):
    class Broken(TailAppendDispatcher):
        def allocate(self, request, fleet_snapshots, active_assignments):
            return AllocationDecision(request.req_id, 42, [request.src_floor, request.target_floor], 0.0)

    with pytest.raises(DispatchError, match="unknown elevator"):
        run(make_config(single_car), [req(0, 0.0, 0, 3)], Broken())


def test_idispatcher_is_abstract():
    with pytest.raises(TypeError):
        IDispatcher()  # type: ignore[abstract]


def test_core_modules_have_no_gui_imports():
    code = (
        "import sys, src.simulation, src.models, src.dispatchers.base, src.config_loader, src.request_generator;"
        "bad=[m for m in sys.modules if m.split('.')[0] in ('tkinter','pygame','streamlit','matplotlib') or m.startswith('src.gui')];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
