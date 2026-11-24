"""Simulator entry point.

    python -m src.main --config simulation_config.json --requests data/requests.csv --algorithm MC_DDI \
        [--output-dir outputs] [--gui]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from src.config_loader import ConfigValidationError, load_config
from src.dispatchers import LOCAL_DISPATCHERS, create_dispatcher, create_dispatcher_for_config
from src.dispatchers.esp32_proxy import ESP32DispatcherProxy
from src.comms.transport import WiFiTCPTransport
from src.request_generator import RequestFileError, RequestGenerator
from src.simulation import SimulationEngine, SimulationLogger


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Run the elevator simulation.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--requests", help="Request CSV (3- or 4-column). Generated from the config if omitted.")
    parser.add_argument("--algorithm", choices=sorted(LOCAL_DISPATCHERS), help="Overrides dispatcher.algorithm")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--gui", action="store_true", help="Open the Streamlit dashboard instead of a headless run")
    parser.add_argument("--esp32", metavar="HOST:PORT", help="Run MC-DDI on an ESP32 over Wi-Fi TCP (overrides config)")
    args = parser.parse_args(argv)

    if args.gui:  # the GUI is launched as a separate process; the core never imports it
        import subprocess
        from pathlib import Path

        app = Path(__file__).resolve().parent / "gui" / "app.py"
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--", "--config", args.config])

    try:
        config = load_config(args.config)
        gen = RequestGenerator()
        requests = (
            gen.load_from_file(args.requests, default_weight_kg=config.avg_passenger_weight_kg)
            if args.requests
            else gen.generate(config)
        )
    except (ConfigValidationError, RequestFileError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.esp32:
        host, _, port = args.esp32.rpartition(":")
        dispatcher = ESP32DispatcherProxy(WiFiTCPTransport(), host, port, config.esp32_bridge.timeout_ms)
    elif args.algorithm:
        dispatcher = create_dispatcher(args.algorithm)
    else:
        dispatcher = create_dispatcher_for_config(config)
    engine = SimulationEngine(config, dispatcher, requests)
    logger = SimulationLogger(output_dir=args.output_dir)
    engine.register_observer(logger)
    summary = engine.run(realtime_scale=0.0)
    print(json.dumps(summary, indent=2))
    if isinstance(dispatcher, ESP32DispatcherProxy):
        print(f"ESP32 link stats: {dispatcher.stats}")
        dispatcher.close()
    print(f"Wrote {args.output_dir}/simulation_output.csv and simulation_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
