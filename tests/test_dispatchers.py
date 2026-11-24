"""Phase 4 — RouteEvaluator, FCFS / SCAN / MC-DDI dispatchers, engine integration and the Layer 3
benchmark regression (testing_strategy_markdown.md §2.1, §2.2, §3, §4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config_loader import load_config
from src.benchmark import ALGORITHMS, SCENARIOS, build_scenario, format_table, run_algorithms
from src.dispatchers import (
    FCFSDispatcher,
    MarginalCostDDDispatcher,
    RouteEvaluator,
    SCANDispatcher,
    create_dispatcher,
    insert_or_merge,
)
from src.dispatchers.baselines import scan_insert
from src.models.dto import ElevatorAssignment, Request, SensorSnapshot
from src.models.enums import RequestStatus
from src.models.route_semantics import BOARDING_NOW, braking_floor, plan_route
from src.request_generator import RequestGenerator
from src.simulation import SimulationEngine, SimulationLogger
from tests.conftest import ROOT
from tests.helpers import RecordingObserver

# ============================================================================ helpers


def snap(eid=0, t=0.0, pos=0.0, speed=0.0, weight=0.0, vib=0.0, obstructed=False, operational=True, at_cap=False):
    return SensorSnapshot(eid, t, pos, speed, weight, vib, obstructed, operational, at_cap)


def req(i, t, s, d, w=75.0, status=RequestStatus.ASSIGNED):
    return Request(i, t, s, d, w, status=status)


def onboard(i, t, s, d, w=75.0):
    r = req(i, t, s, d, w, status=RequestStatus.BOARDED)
    r.board_time = t
    return r


@pytest.fixture
def cfg(building_config):
    return building_config


@pytest.fixture
def e0(cfg):
    return cfg.get_elevator_config(0)  # v=2, start=stop=1.5, doors 7 s, C=8, 640 kg


@pytest.fixture
def ev():
    return RouteEvaluator(avg_weight_kg=75.0, weight_buffer_ratio=0.10, soft_max_wait_sec=60.0, fairness_weight=0.05,
                          door_obstruction_delay_sec=4.0)


def empty_assignments(cfg):
    return {e.elevator_id: ElevatorAssignment() for e in cfg.elevators}


def fleet(cfg, t=0.0, overrides=None):
    """Idle snapshots for every car at its initial floor; ``overrides={eid: {field: value}}``."""
    out = []
    for e in cfg.elevators:
        kw = dict(eid=e.elevator_id, t=t, pos=float(e.initial_floor))
        kw.update((overrides or {}).get(e.elevator_id, {}))
        out.append(snap(**kw))
    return out


# ============================================================================ shared route semantics


def test_plan_route_first_occurrence_and_pruning():
    a = req(1, 0.0, 3, 8)
    b = onboard(2, 0.0, 0, 5)
    plan = plan_route([3, 9, 5, 3, 8, 8], [b], [a])
    assert plan.route == [3, 5, 8]  # unused 9, second 3 and second 8 pruned
    assert plan.board_idx == {1: 0} and plan.alight_idx == {2: 1, 1: 2}


def test_plan_route_missing_src_modes():
    a = req(1, 0.0, 4, 8)
    assert plan_route([8], [], [a]).board_idx[1] == BOARDING_NOW  # dispatcher view: boarding right now
    repaired = plan_route([8], [], [a], append_missing_src=True)
    assert repaired.route == [4, 8]  # src appended, then the now-unused leading 8 pruned
    assert repaired.board_idx[1] == 0 and repaired.alight_idx[1] == 1
    assert plan_route([8], [], [a], append_missing_src=True, prune=False).route == [8, 4, 8]


def test_braking_floor_shared_formula():
    assert braking_floor(3.4, 2.0) == 4
    assert braking_floor(3.0, 2.0) == 4  # leaving floor 3
    assert braking_floor(3.4, -2.0) == 3
    assert braking_floor(-0.6, -2.0) == -1
    assert braking_floor(5.0, 0.0) == 5


# ============================================================================ RouteEvaluator: kinematics


@pytest.mark.parametrize("eid", [0, 1, 2])
def test_estimate_leg_time_formula(cfg, eid):
    e = cfg.get_elevator_config(eid)
    for a, b in [(0, 10), (15, -2), (3, 4)]:
        assert RouteEvaluator.estimate_leg_time(e, a, b) == pytest.approx(e.start_time + abs(b - a) / e.max_speed + e.stop_time)
    assert RouteEvaluator.estimate_leg_time(e, 7, 7) == 0.0


def test_simulate_route_times_stationary(ev, e0):
    times = ev.simulate_route_times(snap(t=100.0), e0, [0, 10, 4])
    assert times == pytest.approx([100.0, 115.0, 115.0 + 7 + 1.5 + 3 + 1.5])


def test_simulate_route_times_moving_car_matches_engine(ev, e0):
    moving = snap(t=10.2, pos=3.4, speed=2.0)
    assert ev.simulate_route_times(moving, e0, [6]) == pytest.approx([10.2 + 1.3 + 1.5])  # engine: STOP at 13.0
    assert ev.simulate_route_times(moving, e0, [2]) == pytest.approx([16.0])  # turnaround, engine: 16.0


def test_simulate_route_times_door_obstruction(ev, e0):
    assert ev.simulate_route_times(snap(obstructed=True), e0, [5]) == pytest.approx([4.0 + 1.5 + 2.5 + 1.5])


# ============================================================================ insert_or_merge


def test_insert_or_merge_inserts_and_merges():
    assert insert_or_merge([1, 8], 5, 1) == ([1, 5, 8], 1)
    assert insert_or_merge([1, 8], 1, 1) == ([1, 8], 0)  # merge with previous stop
    assert insert_or_merge([1, 8], 8, 1) == ([1, 8], 1)  # merge with next stop
    assert insert_or_merge([1, 8], 3, 2) == ([1, 8, 3], 2)
    assert insert_or_merge([], 4, 0) == ([4], 0)


def test_insert_or_merge_respects_precedence():
    # drop-off 1 inserted right after pickup at index 0 must not merge back into index 0
    assert insert_or_merge([1, 8], 1, 1, min_merge_index=1) == ([1, 1, 8], 1)


def test_exact_destination_grouping_adds_zero_stops_and_zero_delay(ev, e0):
    existing = req(1, 0.0, 1, 8)
    new = req(2, 0.0, 1, 8)
    s = snap(t=0.0)
    cost = ev.compute_marginal_cost(s, e0, [existing], new, [1, 8], 0.0, base_route=[1, 8])
    alone = ev.simulate_route_times(s, e0, [1, 8])[1]
    assert cost == pytest.approx(alone)  # only the new passenger's own journey; existing delay = 0
    ctx = ev.make_context(s, e0, [existing], [1, 8], 0.0)
    assert len(ev.evaluate_candidate(ctx, new, [1, 8]).plan.route) == 2


def test_marginal_cost_counts_delay_to_existing_passengers(ev, e0):
    existing = onboard(1, 0.0, 0, 10)
    new = req(2, 0.0, 0, 5)
    s = snap(t=0.0)
    base = ev.simulate_route_times(s, e0, [10])[0]
    cand_times = ev.simulate_route_times(s, e0, [5, 10])
    cost = ev.compute_marginal_cost(s, e0, [existing], new, [0, 5, 10], 0.0, base_route=[10])
    # pickup at the car's own floor is immediate; new arrives at 5; existing delayed by the extra stop
    expected_new = ev.simulate_route_times(s, e0, [0, 5, 10])
    assert cost == pytest.approx((expected_new[1] - 0.0) + (expected_new[2] - base))
    assert cand_times[1] > base  # sanity: the extra stop delays the rider to 10
    # derived base route (LLD signature without base_route) gives the same answer
    assert ev.compute_marginal_cost(s, e0, [existing], new, [0, 5, 10], 0.0) == pytest.approx(cost)


def test_fairness_penalty_is_quadratic_in_excess_wait(ev, e0):
    s = snap(t=200.0, pos=0.0)
    fresh = req(1, 200.0, 0, 5)
    old = req(2, 100.0, 0, 5)  # re-dispatched call made 100 s ago: wait already 100 s > 60 s soft cap
    c_fresh = ev.compute_marginal_cost(s, e0, [], fresh, [0, 5], 200.0, base_route=[])
    c_old = ev.compute_marginal_cost(s, e0, [], old, [0, 5], 200.0, base_route=[])
    assert c_old - c_fresh == pytest.approx(100.0 + 0.05 * 40.0**2)


# ============================================================================ proactive capacity check


def test_weight_feasibility_buffer_boundary(ev, e0):
    new = req(9, 0.0, 0, 5)
    limit = 640.0 * 0.9  # 576 kg
    assert ev.check_weight_feasibility(snap(weight=limit - 75.0), e0, [], new, [0, 5])
    assert not ev.check_weight_feasibility(snap(weight=limit - 74.0), e0, [], new, [0, 5])
    # explicit parameters override the evaluator defaults (LLD signature)
    assert ev.check_weight_feasibility(snap(weight=560.0), e0, [], new, [0, 5], avg_weight=75.0, buffer_ratio=0.0)


def test_weight_feasibility_checks_every_stop_while_aboard(ev, e0):
    riders = [onboard(i, 0.0, 0, 9) for i in range(6)]  # 6 x 75 projected (sensor 450 kg)
    w1, w2 = req(10, 0.0, 3, 9), req(11, 0.0, 3, 9)  # two more waiting at floor 3
    new = req(12, 0.0, 3, 12)
    s = snap(weight=450.0)
    assert not ev.check_weight_feasibility(s, e0, riders + [w1, w2], new, [3, 9, 12])  # 9 x 75 = 675 > 576
    assert ev.check_weight_feasibility(s, e0, riders, new, [3, 9, 12])  # 7 x 75 = 525 ok
    # boarding after the drop-off at 9 is fine even with the full group
    assert ev.check_weight_feasibility(s, e0, riders + [w1, w2], req(13, 0.0, 9, 12), [3, 9, 12])


def test_headcount_feasibility(ev, e0):
    riders = [onboard(i, 0.0, 0, 9, w=40.0) for i in range(8)]  # C = 8, light enough for weight
    new = req(20, 0.0, 2, 12)
    s = snap(weight=320.0)
    assert not ev.check_weight_feasibility(s, e0, riders, new, [2, 9, 12])
    assert ev.check_weight_feasibility(s, e0, riders, req(21, 0.0, 9, 12), [9, 12])


def test_capacity_lock_blocks_pickup_before_first_drop_off(ev, e0):
    riders = [onboard(1, 0.0, 0, 6)]
    s = snap(weight=200.0, at_cap=True)
    assert not ev.check_weight_feasibility(s, e0, riders, req(2, 0.0, 0, 9), [0, 6, 9])
    assert ev.check_weight_feasibility(s, e0, riders, req(3, 0.0, 6, 9), [6, 9])  # drop-off frees room first


# ============================================================================ MC-DDI unit behaviour


@pytest.fixture
def mc(cfg):
    d = MarginalCostDDDispatcher()
    d.initialize(cfg)
    return d


def test_mc_ddi_sensor_gate_excludes_faulted_cars(mc, cfg):
    r = req(1, 0.0, 8, 12, status=RequestStatus.PENDING)
    snaps = fleet(cfg, overrides={2: dict(vib=7.5, operational=False)})  # car 2 waits at floor 8 but is faulted
    d = mc.allocate(r, snaps, empty_assignments(cfg))
    assert d.elevator_id != 2
    assert mc.allocate(r, fleet(cfg), empty_assignments(cfg)).elevator_id == 2  # healthy: nearest car wins


def test_mc_ddi_groups_identical_destinations(mc, cfg):
    first = req(1, 0.0, 0, 8)
    assignments = empty_assignments(cfg)
    assignments[1] = ElevatorAssignment([0, 8], [first])
    d = mc.allocate(req(2, 0.0, 0, 8, status=RequestStatus.PENDING), fleet(cfg), assignments)
    assert d.elevator_id == 1 and d.planned_route == [0, 8]  # merged: zero extra stops


def test_mc_ddi_tie_break_lower_id_then_lighter_cabin(mc, cfg):
    r = req(1, 0.0, 0, 5, status=RequestStatus.PENDING)
    assert mc.allocate(r, fleet(cfg), empty_assignments(cfg)).elevator_id == 0  # cars 0 and 1 identical
    heavier0 = fleet(cfg, overrides={0: dict(weight=150.0)})
    assert mc.allocate(r, heavier0, empty_assignments(cfg)).elevator_id == 1


def test_mc_ddi_prunes_car_without_capacity(mc, cfg):
    r = req(1, 0.0, 0, 5, status=RequestStatus.PENDING)
    snaps = fleet(cfg, overrides={0: dict(weight=560.0)})  # 560 + 75 > 576
    assert mc.allocate(r, snaps, empty_assignments(cfg)).elevator_id == 1


def test_mc_ddi_fallback_when_all_cars_full(mc, cfg):
    r = req(1, 0.0, 0, 5, status=RequestStatus.PENDING)
    snaps = fleet(cfg, overrides={0: dict(weight=600.0), 1: dict(weight=600.0), 2: dict(weight=790.0)})
    assignments = empty_assignments(cfg)
    assignments[0] = ElevatorAssignment([12], [onboard(7, 0.0, 0, 12)])
    d = mc.allocate(r, snaps, assignments)
    assert d.elevator_id == 1  # queue empty soonest among the cars (car 0 busy, car 2 farther)
    assert d.planned_route == [0, 5]


def test_mc_ddi_allocate_is_pure_and_deterministic(mc, cfg):
    r = req(1, 10.0, 3, 11, status=RequestStatus.PENDING)
    snaps = fleet(cfg, t=10.0, overrides={0: dict(pos=4.6, speed=2.0)})
    assignments = empty_assignments(cfg)
    assignments[0] = ElevatorAssignment([9], [onboard(5, 0.0, 0, 9)])
    a = mc.allocate(r, snaps, assignments)
    b = MarginalCostDDDispatcher()
    b.initialize(cfg)
    c = b.allocate(r, snaps, assignments)
    assert (a.elevator_id, a.planned_route, a.estimated_cost) == (c.elevator_id, c.planned_route, c.estimated_cost)
    assert assignments[0].stop_queue == [9]  # inputs not mutated


def test_mc_ddi_respects_point_of_no_return(mc, cfg):
    """Car 0 is moving up past floor 3; a pickup at 3 on car 0 would need a turnaround, so the cost
    model must prefer another car that can reach floor 3 sooner."""
    r = req(1, 10.0, 3, 0, status=RequestStatus.PENDING)
    snaps = fleet(cfg, t=10.0, overrides={0: dict(pos=3.6, speed=2.0), 1: dict(pos=0.0), 2: dict(pos=15.0)})
    assignments = empty_assignments(cfg)
    assignments[0] = ElevatorAssignment([12], [onboard(5, 0.0, 0, 12)])
    assert mc.allocate(r, snaps, assignments).elevator_id == 1


def test_mc_ddi_idle_parking(mc, cfg):
    for i in range(5):
        mc.allocate(req(i, float(i), 0, 5 + i, status=RequestStatus.PENDING), fleet(cfg, t=float(i)), empty_assignments(cfg))
    snaps = fleet(cfg, t=10.0, overrides={0: dict(pos=6.0), 1: dict(pos=12.0)})
    assert mc.get_parking_targets(snaps, empty_assignments(cfg), 10.0) == {}  # idle clock starts
    assert mc.get_parking_targets(snaps, empty_assignments(cfg), 39.0) == {}  # < 30 s idle
    targets = mc.get_parking_targets(snaps, empty_assignments(cfg), 40.0)
    assert targets == {0: 0}  # lobby demand; nearest idle car (car 0 at 6) goes, one car per floor
    # issued once per idle period, and no second car is sent to a floor already claimed
    assert mc.get_parking_targets(snaps, empty_assignments(cfg), 41.0) == {}


# ============================================================================ baselines unit behaviour


def test_fcfs_picks_earliest_finishing_car_and_appends(cfg):
    d = FCFSDispatcher()
    d.initialize(cfg)
    assignments = empty_assignments(cfg)
    assignments[0] = ElevatorAssignment([10], [onboard(1, 0.0, 0, 10)])
    assignments[1] = ElevatorAssignment([3], [onboard(2, 0.0, 0, 3)])
    assignments[2] = ElevatorAssignment([15, 0], [req(3, 0.0, 15, 0)])
    dec = d.allocate(req(4, 0.0, 6, 2, status=RequestStatus.PENDING), fleet(cfg), assignments)
    assert dec.elevator_id == 1 and dec.planned_route == [3, 6, 2]


def test_scan_insert_follows_sweep():
    # car at 0 sweeping up to 10 then down to 2: an up call 4->7 slots into the up leg
    q, i = scan_insert([10, 2], 0.0, 4, +1)
    assert (q, i) == ([4, 10, 2], 0)
    q2, _ = scan_insert(q, 0.0, 7, +1, start=1)
    assert q2 == [4, 7, 10, 2]
    # a down call 8->5 waits for the down leg 10 -> 2
    q, i = scan_insert([10, 2], 0.0, 8, -1)
    assert (q, i) == ([10, 8, 2], 1)
    q2, _ = scan_insert(q, 0.0, 5, -1, start=2)
    assert q2 == [10, 8, 5, 2]
    # merges with an existing stop
    assert scan_insert([10, 2], 0.0, 10, +1) == ([10, 2], 0)


def test_scan_picks_shortest_pickup(cfg):
    d = SCANDispatcher()
    d.initialize(cfg)
    dec = d.allocate(req(1, 0.0, 9, 12, status=RequestStatus.PENDING), fleet(cfg), empty_assignments(cfg))
    assert dec.elevator_id == 2  # waiting at floor 8


def test_create_dispatcher_factory():
    assert isinstance(create_dispatcher("MC_DDI"), MarginalCostDDDispatcher)
    assert isinstance(create_dispatcher("SCAN"), SCANDispatcher)
    assert isinstance(create_dispatcher("FCFS"), FCFSDispatcher)
    with pytest.raises(ValueError):
        create_dispatcher("GREEDY")


# ============================================================================ engine integration


@pytest.fixture(scope="module")
def project_requests(building_config):
    return RequestGenerator().generate(building_config)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_full_profile_integration_and_vibration_failover(building_config, project_requests, algorithm):
    engine = SimulationEngine(building_config, create_dispatcher(algorithm), project_requests)
    obs = RecordingObserver(engine.building)
    logger = SimulationLogger(output_dir=None)
    engine.register_observer(obs)
    engine.register_observer(logger)
    summary = engine.run()

    assert summary["completed_requests"] == 200 and summary["algorithm"] == algorithm
    for r in engine.requests:
        assert r.status == RequestStatus.COMPLETED and r.arrival_time > r.board_time >= r.timestamp
    assert obs.safety_violations == []

    fault = building_config.sensors.injected_faults[0]
    log = engine.allocation_log
    assert not [x for x in log if fault.start_time_sec <= x[0] < fault.end_time_sec and x[2] == 1]
    assert [x for x in log if fault.start_time_sec <= x[0] < fault.end_time_sec], "calls during the window"
    assert [x for x in log if x[0] >= fault.end_time_sec and x[2] == 1], "car 1 resumes after the window"


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_all_cars_full_fallback_saturation(make_config, algorithm):
    """Saturate the whole fleet with heavy passengers at once; nothing is dropped, nothing raises."""
    def mut(raw):
        raw["sensors"]["injected_faults"] = []
        raw["sensors"]["door_obstruction_probability"] = 0.0

    cfg = make_config(mut)
    burst = [Request(i, 0.0 + 0.01 * i, 0, 5 + i % 10, 140.0) for i in range(40)]  # 5600 kg vs 2080 kg fleet
    engine = SimulationEngine(cfg, create_dispatcher(algorithm), burst)
    obs = RecordingObserver(engine.building)
    engine.register_observer(obs)
    summary = engine.run()
    assert summary["completed_requests"] == 40 and not engine.stalled
    assert obs.safety_violations == []


def test_mc_ddi_parking_engine_integration(make_config):
    def mut(raw):
        raw["sensors"]["injected_faults"] = []
        raw["sensors"]["door_obstruction_probability"] = 0.0
        raw["elevators"][0]["initial_floor"] = 10

    cfg = make_config(mut)
    reqs = [Request(i, float(i), 0, 3 + i, 75.0) for i in range(3)] + [Request(3, 200.0, 0, 9, 75.0)]
    engine = SimulationEngine(cfg, create_dispatcher("MC_DDI"), reqs)
    engine.run()
    # after the early lobby calls, idle cars were repositioned; the late lobby call is served instantly
    assert engine.requests[3].wait_time == pytest.approx(0.0)


# ============================================================================ Layer 3: benchmark regression

BENCH_SEEDS = (42, 1, 2, 3, 7)
_REPORT = ROOT / "outputs" / "phase4_benchmark.md"


@pytest.fixture(scope="module")
def raw_project_config():
    return json.loads((ROOT / "simulation_config.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bench(raw_project_config):
    """{(scenario, seed): {algorithm: summary}} for the 4 scenarios at seed 42 plus extra seeds for the
    up-peak and full-day acceptance scenarios."""
    results = {}
    for scenario in SCENARIOS:
        seeds = BENCH_SEEDS if scenario in ("up_peak", "full") else (42,)
        for seed in seeds:
            cfg = build_scenario(raw_project_config, scenario, seed)
            results[(scenario, seed)] = run_algorithms(cfg, RequestGenerator().generate(cfg))
    _REPORT.parent.mkdir(exist_ok=True)
    rows = ["# Phase 4 benchmark (exact numbers)", ""]
    rows.append(format_table({f"{s} (seed {sd})": r for (s, sd), r in results.items()}))
    rows += ["", "## MC_DDI vs SCAN total stops", "", "| Scenario | Seed | MC_DDI | SCAN | Δ |", "| :--- | ---: | ---: | ---: | ---: |"]
    for (s, sd), r in results.items():
        m, sc = r["MC_DDI"]["total_elevator_stops"], r["SCAN"]["total_elevator_stops"]
        rows.append(f"| {s} | {sd} | {m} | {sc} | {m - sc:+d} |")
    _REPORT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("\n" + "\n".join(rows))
    return results


@pytest.mark.benchmark
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_bench_mc_ddi_lowest_avg_journey_time(bench, scenario):
    r = bench[(scenario, 42)]
    mc = r["MC_DDI"]["avg_journey_time_sec"]
    assert mc < r["FCFS"]["avg_journey_time_sec"], r
    assert mc < r["SCAN"]["avg_journey_time_sec"], r


@pytest.mark.benchmark
@pytest.mark.parametrize("scenario", ["up_peak", "full"])
@pytest.mark.parametrize("seed", BENCH_SEEDS)
def test_bench_mc_ddi_journey_time_robust_across_seeds(bench, scenario, seed):
    r = bench[(scenario, seed)]
    assert r["MC_DDI"]["avg_journey_time_sec"] < min(r["FCFS"]["avg_journey_time_sec"], r["SCAN"]["avg_journey_time_sec"])


@pytest.mark.benchmark
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_bench_mc_ddi_fewer_stops_than_fcfs(bench, scenario):
    r = bench[(scenario, 42)]
    assert r["MC_DDI"]["total_elevator_stops"] < r["FCFS"]["total_elevator_stops"], r


@pytest.mark.benchmark
def test_bench_mc_ddi_stops_not_above_scan_on_up_peak(bench):
    r = bench[("up_peak", 42)]
    assert r["MC_DDI"]["total_elevator_stops"] <= r["SCAN"]["total_elevator_stops"], (
        f"MC_DDI {r['MC_DDI']['total_elevator_stops']} stops vs SCAN {r['SCAN']['total_elevator_stops']}"
    )


@pytest.mark.benchmark
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_bench_no_starvation_and_all_completed(bench, scenario, building_config):
    r = bench[(scenario, 42)]
    for algo, s in r.items():
        assert s["completed_requests"] == s["total_requests"], algo
    assert r["MC_DDI"]["max_hall_wait_time_sec"] <= 2 * building_config.dispatcher.soft_max_wait_sec


# ============================================================================ CLI entry point


def test_main_cli_headless_run(tmp_path, capsys):
    from src.main import main

    req_path = tmp_path / "requests.csv"
    gen = RequestGenerator()
    gen.save_to_file(gen.generate(load_config(ROOT / "simulation_config.json"))[:30], req_path, strict_3col=True)
    rc = main(["--config", str(ROOT / "simulation_config.json"), "--requests", str(req_path),
               "--algorithm", "SCAN", "--output-dir", str(tmp_path / "out")])
    assert rc == 0
    summary = json.loads((tmp_path / "out" / "simulation_summary.json").read_text())
    assert summary["algorithm"] == "SCAN" and summary["completed_requests"] == 30
    assert (tmp_path / "out" / "simulation_output.csv").read_text().startswith("time,src,target,elevator_id,event_type\n")
