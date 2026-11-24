"""Benchmark scenarios (testing_strategy_markdown.md Layer 3), shared by the test-suite and Phase 7.

Scenarios: ``up_peak``, ``midday``, ``down_peak`` (each a single phase of the project profile run on its
own over 1200 s, without the injected fault) and ``full`` (the unmodified 3-phase profile).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from src.config_loader import parse_config
from src.dispatchers import create_dispatcher
from src.models.dto import BuildingConfig, Request
from src.request_generator import RequestGenerator
from src.simulation import SimulationEngine

SCENARIOS = ("up_peak", "midday", "down_peak", "full")
ALGORITHMS = ("FCFS", "SCAN", "MC_DDI")
_PHASE_INDEX = {"up_peak": 0, "midday": 1, "down_peak": 2}


def build_scenario(raw_config: dict, scenario: str, seed: Optional[int] = None) -> BuildingConfig:
    raw = copy.deepcopy(raw_config)
    if seed is not None:
        raw["simulation"]["random_seed"] = seed
    if scenario != "full":
        phase = dict(raw["traffic_generator"]["phases"][_PHASE_INDEX[scenario]])
        length = phase["end_time_sec"] - phase["start_time_sec"]
        phase["start_time_sec"], phase["end_time_sec"] = 0.0, length
        raw["traffic_generator"]["phases"] = [phase]
        raw["traffic_generator"]["total_requests"] = round(phase["arrival_rate_per_min"] * length / 60.0)
        raw["simulation"]["duration_sec"] = length
        raw["sensors"]["injected_faults"] = []
    return parse_config(raw)


def run_algorithms(
    config: BuildingConfig, requests: List[Request], algorithms: Iterable[str] = ALGORITHMS
) -> Dict[str, dict]:
    return {a: SimulationEngine(config, create_dispatcher(a), requests).run() for a in algorithms}


def run_benchmark(
    config_path: Union[str, Path], scenarios: Iterable[str] = SCENARIOS, seed: Optional[int] = None
) -> Dict[str, Dict[str, dict]]:
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    results = {}
    for name in scenarios:
        cfg = build_scenario(raw, name, seed)
        results[name] = run_algorithms(cfg, RequestGenerator().generate(cfg))
    return results


def format_table(results: Dict[str, Dict[str, dict]]) -> str:
    lines = [
        "| Scenario | Algorithm | Avg Journey (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Overloads |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scen, by_algo in results.items():
        for algo, s in by_algo.items():
            lines.append(
                f"| {scen} | {algo} | {s['avg_journey_time_sec']:.2f} | {s['avg_hall_wait_time_sec']:.2f} | "
                f"{s['max_hall_wait_time_sec']:.2f} | {s['total_elevator_stops']} | {s['overload_events']} |"
            )
    return "\n".join(lines)
