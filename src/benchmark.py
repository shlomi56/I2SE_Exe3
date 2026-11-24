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


# ============================================================================ CLI: full benchmark run

SCENARIO_TITLES = {
    "full": "Full day (3 phases, E1 vibration fault 1200–1320 s)",
    "up_peak": "Morning up-peak (75 % lobby → floors)",
    "midday": "Midday mixed (50 % inter-floor)",
    "down_peak": "Evening down-peak (75 % floors → lobby)",
}


def _run_logged(config: BuildingConfig, requests: List[Request], dispatcher, out_dir: Path) -> dict:
    from src.simulation import SimulationLogger

    engine = SimulationEngine(config, dispatcher, requests)
    engine.register_observer(SimulationLogger(output_dir=out_dir))
    return engine.run()


def _esp32_mock_dispatcher():
    """MC-DDI executed by the real esp32/ firmware served on 127.0.0.1 (host-side mock ESP32)."""
    import sys
    import threading

    esp32_dir = str(Path(__file__).resolve().parent.parent / "esp32")
    if esp32_dir not in sys.path:
        sys.path.insert(0, esp32_dir)
    from edge_node import EdgeNode  # noqa: E402  (firmware module)
    from wireless_server import WirelessServer  # noqa: E402

    from src.comms.transport import WiFiTCPTransport
    from src.dispatchers.esp32_proxy import ESP32DispatcherProxy

    server = WirelessServer(port=0, host="127.0.0.1", handler=EdgeNode().handle_line)
    server.begin()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy = ESP32DispatcherProxy(WiFiTCPTransport(), "127.0.0.1", str(server.port), 500)
    return proxy, server


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from src.dispatchers import create_dispatcher as _create
    from src.request_generator import RequestGenerator as _Gen

    parser = argparse.ArgumentParser(prog="python -m src.benchmark", description="Run the FCFS / SCAN / MC-DDI benchmark.")
    parser.add_argument("--config", default="simulation_config.json")
    parser.add_argument("--output-dir", default="outputs/benchmarks")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--seeds", type=int, nargs="*", default=[1, 2, 3, 7], help="extra seeds for the robustness table")
    parser.add_argument("--esp32-mock", action="store_true", help="also run MC-DDI through the ESP32 firmware over local TCP")
    args = parser.parse_args(argv)

    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    base_seed = raw["simulation"]["random_seed"]
    out_root, data_dir = Path(args.output_dir), Path(args.data_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    gen = _Gen()

    results: Dict[str, Dict[str, dict]] = {}
    for scen in SCENARIOS:
        cfg = build_scenario(raw, scen)
        reqs = gen.generate(cfg)
        name = "requests.csv" if scen == "full" else f"requests_{scen}.csv"
        gen.save_to_file(reqs, data_dir / name)
        results[scen] = {}
        for algo in ALGORITHMS:
            results[scen][algo] = _run_logged(cfg, reqs, _create(algo), out_root / scen / algo)
            print(f"{scen:9s} {algo:6s} avg journey {results[scen][algo]['avg_journey_time_sec']:6.2f} s  "
                  f"stops {results[scen][algo]['total_elevator_stops']}")
        if scen == "full" and args.esp32_mock:
            proxy, server = _esp32_mock_dispatcher()
            try:
                s = _run_logged(cfg, reqs, proxy, out_root / scen / "MC_DDI_ESP32_MOCK")
            finally:
                proxy.close()
                server.stop()
            s["esp32_link_stats"] = dict(proxy.stats)
            results[scen]["MC_DDI (ESP32 mock)"] = s
            print(f"{scen:9s} MC_DDI via ESP32 firmware mock: avg journey {s['avg_journey_time_sec']:.2f} s, "
                  f"latency {s['avg_dispatch_latency_ms']:.3f} ms, link {proxy.stats}")

    robustness: Dict[str, Dict[str, Dict[str, dict]]] = {}
    for scen in ("up_peak", "full"):
        robustness[scen] = {str(base_seed): {a: results[scen][a] for a in ALGORITHMS}}
        for seed in args.seeds:
            cfg = build_scenario(raw, scen, seed)
            robustness[scen][str(seed)] = run_algorithms(cfg, gen.generate(cfg))

    (out_root / "benchmark_results.json").write_text(
        json.dumps({"seed": base_seed, "scenarios": results, "robustness": robustness}, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "benchmark_results.md").write_text(render_report(results, robustness, base_seed), encoding="utf-8")
    print(f"\nWrote {out_root}/benchmark_results.md, benchmark_results.json and per-run CSV/JSON; requests in {data_dir}/")
    return 0


def render_report(results: Dict[str, Dict[str, dict]], robustness: Dict[str, Dict[str, Dict[str, dict]]], seed: int) -> str:
    lines = [f"# Benchmark results (seed {seed})", ""]
    for scen, by_algo in results.items():
        lines += [f"## {SCENARIO_TITLES[scen]}", "",
                  "| Algorithm | Execution | Avg Journey (s) | Avg Hall Wait (s) | Max Hall Wait (s) | Total Stops | Overloads | Avg Dispatch Latency (ms) |",
                  "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for algo, s in by_algo.items():
            lines.append(
                f"| {algo} | {s['execution_mode']} | {s['avg_journey_time_sec']:.2f} | {s['avg_hall_wait_time_sec']:.2f} | "
                f"{s['max_hall_wait_time_sec']:.2f} | {s['total_elevator_stops']} | {s['overload_events']} | "
                f"{s['avg_dispatch_latency_ms']:.3f} |"
            )
        lines.append("")
    lines += ["## Robustness across seeds (avg journey s / total stops)", "",
              "| Scenario | Seed | FCFS | SCAN | MC_DDI |", "| :--- | ---: | :--- | :--- | :--- |"]
    for scen, by_seed in robustness.items():
        for sd, by_algo in by_seed.items():
            cells = " | ".join(f"{by_algo[a]['avg_journey_time_sec']:.2f} / {by_algo[a]['total_elevator_stops']}" for a in ALGORITHMS)
            lines.append(f"| {scen} | {sd} | {cells} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
