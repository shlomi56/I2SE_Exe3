"""Simulator entry point.

    python -m src.main --config simulation_config.json --requests data/requests.csv --algorithm MC_DDI \
        [--output-dir outputs]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from src.config_loader import ConfigValidationError, load_config
from src.dispatchers import LOCAL_DISPATCHERS, create_dispatcher
from src.request_generator import RequestFileError, RequestGenerator
from src.simulation import SimulationEngine, SimulationLogger


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Run the elevator simulation.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--requests", help="Request CSV (3- or 4-column). Generated from the config if omitted.")
    parser.add_argument("--algorithm", choices=sorted(LOCAL_DISPATCHERS), help="Overrides dispatcher.algorithm")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args(argv)

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

    algorithm = args.algorithm or config.dispatcher.algorithm
    engine = SimulationEngine(config, create_dispatcher(algorithm), requests)
    logger = SimulationLogger(output_dir=args.output_dir)
    engine.register_observer(logger)
    summary = engine.run(realtime_scale=0.0)
    print(json.dumps(summary, indent=2))
    print(f"Wrote {args.output_dir}/simulation_output.csv and simulation_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
