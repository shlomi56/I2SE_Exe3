"""Time-based request auto-generator (request_format_auto_generator_spec_markdown.md).

Programmatic API::

    RequestGenerator().generate(config, seed=None) -> List[Request]

CLI::

    python -m src.request_generator --config simulation_config.json --output data/requests.csv \
        [--seed 42] [--strict-3col]
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import random
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from src.config_loader import ConfigValidationError, load_config
from src.models.dto import BuildingConfig, Request, TrafficPhase
from src.models.enums import TrafficType

DEFAULT_AVG_WEIGHT_KG = 75.0
HEADER_3COL = ["time", "src", "target"]
HEADER_4COL = ["time", "src", "target", "weight_kg"]


class RequestFileError(ValueError):
    """Raised when a request CSV violates the documented schema."""


def allocate_largest_remainder(total: int, weights: Sequence[float]) -> List[int]:
    """Split ``total`` into integer counts proportional to ``weights`` (Hamilton / largest remainder).

    Ties in the fractional remainder are broken by the lower index, so the result is deterministic.
    """
    if total < 0:
        raise ValueError("total must be >= 0")
    weight_sum = float(sum(weights))
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive value")
    quotas = [total * w / weight_sum for w in weights]
    counts = [math.floor(q) for q in quotas]
    remainder = total - sum(counts)
    order = sorted(range(len(weights)), key=lambda i: (-(quotas[i] - counts[i]), i))
    for i in order[:remainder]:
        counts[i] += 1
    return counts


class RequestGenerator:
    """Synthesizes reproducible ``(time, src, target, weight_kg)`` call streams from the JSON config."""

    # ---------------------------------------------------------------- generation

    def generate(self, config: BuildingConfig, seed: Optional[int] = None) -> List[Request]:
        """Generate time-ordered calls. ``seed`` overrides ``simulation.random_seed`` when given."""
        return [req for req, _ in self.generate_labeled(config, seed)]

    def generate_labeled(
        self, config: BuildingConfig, seed: Optional[int] = None
    ) -> List[Tuple[Request, TrafficType]]:
        """Same as :meth:`generate`, also returning each call's sampled traffic type (for testing)."""
        rng = random.Random(config.simulation.random_seed if seed is None else seed)
        phases = config.traffic.phases  # already sorted by start time and non-overlapping
        total = config.traffic.total_requests

        if total:  # Mode 1 — exact total_requests target
            counts = allocate_largest_remainder(total, [ph.rate_per_sec * ph.duration_sec for ph in phases])
        else:  # Mode 2 — open-ended
            counts = [None] * len(phases)

        labeled: List[Tuple[float, TrafficType, int, int, float]] = []
        for phase, n in zip(phases, counts):
            for t in self._phase_timestamps(rng, phase, n, config.traffic.distribution):
                ttype, src, target = self._sample_od(rng, phase, config)
                weight = self._sample_weight(rng, config)
                labeled.append((t, ttype, src, target, weight))

        labeled.sort(key=lambda row: row[0])  # stable: phase order preserved for equal times
        return [
            (Request(req_id=i, timestamp=t, src_floor=s, target_floor=d, actual_weight_kg=w), ttype)
            for i, (t, ttype, s, d, w) in enumerate(labeled)
        ]

    @staticmethod
    def _phase_timestamps(
        rng: random.Random, phase: TrafficPhase, n: Optional[int], distribution: str
    ) -> List[float]:
        a, b = phase.start_time_sec, phase.end_time_sec
        if n is None:  # Mode 2
            if distribution == "UNIFORM":
                n = int(round(phase.rate_per_sec * phase.duration_sec))
            else:
                times: List[float] = []
                t = a
                while True:
                    t += rng.expovariate(phase.rate_per_sec)
                    if t > b:
                        break
                    times.append(round(t, 2))
                return times
        if n == 0:
            return []
        if distribution == "UNIFORM":
            step = (b - a) / n
            raw = [a + (k + 0.5) * step for k in range(n)]
        else:  # POISSON, conditional on N arrivals: i.i.d. Uniform(a, b), sorted
            raw = sorted(rng.uniform(a, b) for _ in range(n))
        return [round(t, 2) for t in raw]

    @staticmethod
    def _sample_od(
        rng: random.Random, phase: TrafficPhase, config: BuildingConfig
    ) -> Tuple[TrafficType, int, int]:
        lobby = config.lobby_floor
        floors = config.floors
        non_lobby = [f for f in floors if f != lobby]
        u = rng.random()
        if u < phase.up_peak_ratio:
            return TrafficType.UP_PEAK, lobby, rng.choice(non_lobby)
        if u < phase.up_peak_ratio + phase.down_peak_ratio:
            return TrafficType.DOWN_PEAK, rng.choice(non_lobby), lobby
        src, target = rng.sample(floors, 2)
        return TrafficType.INTER_FLOOR, src, target

    @staticmethod
    def _sample_weight(rng: random.Random, config: BuildingConfig) -> float:
        p = config.passengers
        w = rng.gauss(config.avg_passenger_weight_kg, p.weight_std_kg)
        return round(min(max(w, p.min_weight_kg), p.max_weight_kg), 1)

    # ---------------------------------------------------------------- file I/O

    @staticmethod
    def to_csv_text(requests: List[Request], strict_3col: bool = False) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(HEADER_3COL if strict_3col else HEADER_4COL)
        for r in requests:
            row = [f"{r.timestamp:.2f}", r.src_floor, r.target_floor]
            if not strict_3col:
                row.append(f"{r.actual_weight_kg:.1f}")
            writer.writerow(row)
        return buf.getvalue()

    def save_to_file(self, requests: List[Request], filepath: Union[str, Path], strict_3col: bool = False) -> None:
        """Export the request list as UTF-8 CSV (4-column by default, 3-column with ``strict_3col``)."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.to_csv_text(requests, strict_3col))

    def load_from_file(
        self, filepath: Union[str, Path], default_weight_kg: Optional[float] = None
    ) -> List[Request]:
        """Load a 3-column (``time,src,target``) or 4-column (``…,weight_kg``) request CSV.

        Missing weights fall back to ``default_weight_kg`` (pass ``config.avg_passenger_weight_kg``),
        or 75.0 kg if not given. ``req_id`` is the 0-based data-row index.
        """
        fallback = DEFAULT_AVG_WEIGHT_KG if default_weight_kg is None else float(default_weight_kg)
        path = Path(filepath)
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise RequestFileError(f"{path}: file is empty (header row required)")
            fields = [f.strip() for f in reader.fieldnames]
            missing = [c for c in HEADER_3COL if c not in fields]
            if missing:
                raise RequestFileError(f"{path}: missing required column(s) {missing}; header was {fields}")
            reader.fieldnames = fields
            has_weight = "weight_kg" in fields

            requests: List[Request] = []
            prev_t = -math.inf
            for line_no, row in enumerate(reader, start=2):
                try:
                    t = float(row["time"])
                    src = int(row["src"])
                    target = int(row["target"])
                    wtxt = (row.get("weight_kg") or "").strip() if has_weight else ""
                    weight = float(wtxt) if wtxt else fallback
                except (TypeError, ValueError) as exc:
                    raise RequestFileError(f"{path}:{line_no}: malformed row {row}") from exc
                if t < 0 or not math.isfinite(t):
                    raise RequestFileError(f"{path}:{line_no}: time must be >= 0 (got {t})")
                if t < prev_t:
                    raise RequestFileError(f"{path}:{line_no}: rows must be sorted by time ({t} < {prev_t})")
                if src == target:
                    raise RequestFileError(f"{path}:{line_no}: src must differ from target (both {src})")
                if weight <= 0:
                    raise RequestFileError(f"{path}:{line_no}: weight_kg must be > 0 (got {weight})")
                prev_t = t
                requests.append(Request(len(requests), t, src, target, weight))
        return requests


# -------------------------------------------------------------------------------------- CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.request_generator",
        description="Generate a time-based (time, src, target) elevator request list from simulation_config.json.",
    )
    parser.add_argument("--config", required=True, help="Path to simulation_config.json")
    parser.add_argument("--output", required=True, help="Output CSV path (e.g. data/requests.csv)")
    parser.add_argument("--seed", type=int, default=None, help="Override simulation.random_seed")
    parser.add_argument(
        "--strict-3col", action="store_true", help="Omit weight_kg and emit a bare time,src,target file"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigValidationError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    gen = RequestGenerator()
    requests = gen.generate(config, seed=args.seed)
    gen.save_to_file(requests, args.output, strict_3col=args.strict_3col)
    print(f"Wrote {len(requests)} requests to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
