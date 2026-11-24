"""Phase 2 — RequestGenerator: the 5 output invariants (generator spec §4), CSV format compatibility,
both arrival modes and the CLI contract."""
from __future__ import annotations

import csv
import math
import subprocess
import sys
from collections import Counter

import pytest

from src.models.enums import TrafficType
from src.request_generator import (
    HEADER_3COL,
    HEADER_4COL,
    RequestFileError,
    RequestGenerator,
    allocate_largest_remainder,
    main,
)
from tests.conftest import CONFIG_PATH, ROOT


@pytest.fixture
def gen() -> RequestGenerator:
    return RequestGenerator()


def _read_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


# ============================================================ Invariant 1: exact row count (Mode 1)


def test_inv1_row_count_matches_total_requests(gen, building_config, tmp_path):
    reqs = gen.generate(building_config)
    assert len(reqs) == building_config.traffic.total_requests == 200
    out = tmp_path / "requests.csv"
    gen.save_to_file(reqs, out)
    rows = _read_rows(out)
    assert rows[0] == HEADER_4COL
    assert len(rows) - 1 == 200


def test_inv1_phase_counts_follow_largest_remainder(gen, building_config):
    labeled = gen.generate_labeled(building_config)
    phases = building_config.traffic.phases
    # Expected volumes 120 / 60 / 100 (of 280) scaled to 200 -> 85.71 / 42.86 / 71.43 -> 86 / 43 / 71
    expected = allocate_largest_remainder(200, [p.rate_per_sec * p.duration_sec for p in phases])
    assert expected == [86, 43, 71]
    for phase, n in zip(phases, expected):
        in_phase = [r for r, _ in labeled if phase.start_time_sec <= r.timestamp <= phase.end_time_sec]
        assert len(in_phase) == n


@pytest.mark.parametrize(
    "total, weights, expected",
    [
        (10, [1, 1, 1], [4, 3, 3]),  # tie on remainder -> lower index wins
        (7, [0.5, 0.25, 0.25], [3, 2, 2]),  # remainders .5/.75/.75
        (0, [1, 2], [0, 0]),
        (5, [1], [5]),
    ],
)
def test_largest_remainder(total, weights, expected):
    assert allocate_largest_remainder(total, weights) == expected
    assert sum(allocate_largest_remainder(total, weights)) == total


# ============================================================ Invariant 2: sorted, within horizon


def test_inv2_timestamps_sorted_and_within_horizon(gen, building_config):
    reqs = gen.generate(building_config)
    times = [r.timestamp for r in reqs]
    assert times == sorted(times)
    assert all(0.0 <= t <= building_config.simulation.duration_sec for t in times)
    assert all(round(t, 2) == t for t in times)  # 2-decimal precision
    assert [r.req_id for r in reqs] == list(range(len(reqs)))


# ============================================================ Invariant 3: floor bounds, src != target


def test_inv3_floor_bounds_and_distinct_od(gen, building_config):
    lo, hi = building_config.min_floor, building_config.max_floor
    for r in gen.generate(building_config):
        assert lo <= r.src_floor <= hi
        assert lo <= r.target_floor <= hi
        assert r.src_floor != r.target_floor


def test_inv3_traffic_type_semantics(gen, building_config):
    lobby = building_config.lobby_floor
    for req, ttype in gen.generate_labeled(building_config):
        if ttype is TrafficType.UP_PEAK:
            assert req.src_floor == lobby and req.target_floor != lobby
        elif ttype is TrafficType.DOWN_PEAK:
            assert req.target_floor == lobby and req.src_floor != lobby


def test_weights_clamped_and_rounded(gen, building_config):
    p = building_config.passengers
    weights = [r.actual_weight_kg for r in gen.generate(building_config)]
    assert all(p.min_weight_kg <= w <= p.max_weight_kg for w in weights)
    assert all(round(w, 1) == w for w in weights)
    mean = sum(weights) / len(weights)
    assert abs(mean - building_config.avg_passenger_weight_kg) < 5.0  # 200 samples, sigma 15 -> SE ~1.06


# ============================================================ Invariant 4: byte-for-byte determinism


def test_inv4_byte_identical_across_runs(gen, building_config, tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    gen.save_to_file(gen.generate(building_config), a)
    gen.save_to_file(RequestGenerator().generate(building_config), b)
    assert a.read_bytes() == b.read_bytes()


def test_inv4_seed_override_changes_output(gen, building_config):
    base = RequestGenerator.to_csv_text(gen.generate(building_config))
    same = RequestGenerator.to_csv_text(gen.generate(building_config, seed=building_config.simulation.random_seed))
    other = RequestGenerator.to_csv_text(gen.generate(building_config, seed=7))
    assert base == same
    assert base != other


def test_inv4_cli_runs_are_byte_identical(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    for out in (a, b):
        proc = subprocess.run(
            [sys.executable, "-m", "src.request_generator", "--config", str(CONFIG_PATH), "--output", str(out)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
    assert a.read_bytes() == b.read_bytes()


# ============================================================ Invariant 5: phase ratios within ±3σ


def _assert_ratios_within_3sigma(labeled, phases):
    for phase in phases:
        types = [t for r, t in labeled if phase.start_time_sec <= r.timestamp <= phase.end_time_sec]
        n = len(types)
        assert n > 0
        counts = Counter(types)
        for ttype, p in (
            (TrafficType.UP_PEAK, phase.up_peak_ratio),
            (TrafficType.DOWN_PEAK, phase.down_peak_ratio),
            (TrafficType.INTER_FLOOR, phase.inter_floor_ratio),
        ):
            sigma = math.sqrt(n * p * (1 - p))
            assert abs(counts[ttype] - n * p) <= 3 * sigma + 1e-9, (
                f"{phase.name} {ttype.value}: {counts[ttype]} of {n}, expected {n * p:.1f} ± {3 * sigma:.1f}"
            )


def test_inv5_phase_ratios_on_project_config(gen, building_config):
    _assert_ratios_within_3sigma(gen.generate_labeled(building_config), building_config.traffic.phases)


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 1234])
def test_inv5_phase_ratios_large_sample(gen, make_config, seed):
    def mut(raw):
        raw["traffic_generator"]["total_requests"] = 6000

    cfg = make_config(mut)
    _assert_ratios_within_3sigma(gen.generate_labeled(cfg, seed=seed), cfg.traffic.phases)


def test_pure_inter_floor_phase(gen, make_config):
    def mut(raw):
        for ph in raw["traffic_generator"]["phases"]:
            ph.update(up_peak_ratio=0.0, down_peak_ratio=0.0, inter_floor_ratio=1.0)

    labeled = gen.generate_labeled(make_config(mut))
    assert {t for _, t in labeled} == {TrafficType.INTER_FLOOR}


# ============================================================ Arrival modes


def test_uniform_distribution_spacing(gen, make_config):
    def mut(raw):
        raw["traffic_generator"]["distribution"] = "UNIFORM"

    cfg = make_config(mut)
    reqs = gen.generate(cfg)
    assert len(reqs) == 200
    first_phase = [r.timestamp for r in reqs if r.timestamp < 1200.0]
    gaps = {round(b - a, 2) for a, b in zip(first_phase, first_phase[1:])}
    assert max(gaps) - min(gaps) <= 0.011  # evenly spaced up to 2-decimal rounding


@pytest.mark.parametrize("total", [None, 0])
def test_mode2_open_ended_poisson(gen, make_config, total):
    def mut(raw):
        raw["traffic_generator"]["total_requests"] = total

    cfg = make_config(mut)
    reqs = gen.generate(cfg)
    expected = sum(p.rate_per_sec * p.duration_sec for p in cfg.traffic.phases)  # 280
    assert abs(len(reqs) - expected) <= 4 * math.sqrt(expected)
    times = [r.timestamp for r in reqs]
    assert times == sorted(times)
    assert all(0.0 <= t <= cfg.simulation.duration_sec for t in times)
    assert RequestGenerator.to_csv_text(reqs) == RequestGenerator.to_csv_text(gen.generate(cfg))


# ============================================================ 3-column / 4-column compatibility


def test_strict_3col_output_format(gen, building_config, tmp_path):
    out = tmp_path / "r3.csv"
    reqs = gen.generate(building_config)
    gen.save_to_file(reqs, out, strict_3col=True)
    rows = _read_rows(out)
    assert rows[0] == HEADER_3COL
    assert all(len(r) == 3 for r in rows)
    assert rows[1] == [f"{reqs[0].timestamp:.2f}", str(reqs[0].src_floor), str(reqs[0].target_floor)]


def test_roundtrip_4col_preserves_everything(gen, building_config, tmp_path):
    out = tmp_path / "r4.csv"
    reqs = gen.generate(building_config)
    gen.save_to_file(reqs, out)
    loaded = gen.load_from_file(out)
    assert [(r.req_id, r.timestamp, r.src_floor, r.target_floor, r.actual_weight_kg) for r in loaded] == [
        (r.req_id, r.timestamp, r.src_floor, r.target_floor, r.actual_weight_kg) for r in reqs
    ]


def test_roundtrip_3col_falls_back_to_avg_weight(gen, building_config, tmp_path):
    out = tmp_path / "r3.csv"
    reqs = gen.generate(building_config)
    gen.save_to_file(reqs, out, strict_3col=True)
    loaded = gen.load_from_file(out, default_weight_kg=building_config.avg_passenger_weight_kg)
    assert len(loaded) == len(reqs)
    assert all(r.actual_weight_kg == building_config.avg_passenger_weight_kg for r in loaded)
    assert [(r.timestamp, r.src_floor, r.target_floor) for r in loaded] == [
        (r.timestamp, r.src_floor, r.target_floor) for r in reqs
    ]


def test_load_spec_example_file(gen, tmp_path):
    """The literal example from request_format_auto_generator_spec_markdown.md §1.2."""
    f = tmp_path / "example.csv"
    f.write_text(
        "time,src,target,weight_kg\n4.12,0,11,72.4\n9.85,0,4,88.1\n15.30,0,11,64.0\n28.44,7,0,79.5\n41.09,3,12,102.3\n"
    )
    loaded = gen.load_from_file(f)
    assert [(r.timestamp, r.src_floor, r.target_floor, r.actual_weight_kg) for r in loaded] == [
        (4.12, 0, 11, 72.4),
        (9.85, 0, 4, 88.1),
        (15.30, 0, 11, 64.0),
        (28.44, 7, 0, 79.5),
        (41.09, 3, 12, 102.3),
    ]


def test_load_external_3col_file_with_basement_floors_and_default_weight(gen, tmp_path):
    f = tmp_path / "external.csv"
    f.write_text("time, src, target\n0.0,-2,5\n0.0,5,-1\n3.5,0,15\n")  # padded header, equal times
    loaded = gen.load_from_file(f)
    assert [(r.src_floor, r.target_floor) for r in loaded] == [(-2, 5), (5, -1), (0, 15)]
    assert all(r.actual_weight_kg == 75.0 for r in loaded)


@pytest.mark.parametrize(
    "content, msg",
    [
        ("", "empty"),
        ("time,src\n1.0,0\n", "missing required column"),
        ("time,src,target\n2.0,0,5\n1.0,0,5\n", "sorted"),
        ("time,src,target\n1.0,3,3\n", "differ"),
        ("time,src,target\n-1.0,0,3\n", ">= 0"),
        ("time,src,target\nabc,0,3\n", "malformed"),
        ("time,src,target,weight_kg\n1.0,0,3,-5\n", "weight_kg"),
    ],
)
def test_load_rejects_invalid_files(gen, tmp_path, content, msg):
    f = tmp_path / "bad.csv"
    f.write_text(content)
    with pytest.raises(RequestFileError, match=msg):
        gen.load_from_file(f)


# ============================================================ CLI


def test_cli_strict_3col_and_seed(tmp_path, capsys):
    out = tmp_path / "nested" / "requests.csv"
    rc = main(["--config", str(CONFIG_PATH), "--output", str(out), "--seed", "42", "--strict-3col"])
    assert rc == 0
    assert "Wrote 200 requests" in capsys.readouterr().out
    rows = _read_rows(out)
    assert rows[0] == HEADER_3COL and len(rows) == 201


def test_cli_rejects_invalid_config(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": "1.0"}')
    rc = main(["--config", str(bad), "--output", str(tmp_path / "x.csv")])
    assert rc == 2
    assert "Config error" in capsys.readouterr().err
    assert not (tmp_path / "x.csv").exists()
