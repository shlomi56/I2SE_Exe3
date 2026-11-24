"""``SimulationLogger`` observer and KPI summary (simulator_output_format_spec_markdown.md).

``simulation_output.csv`` columns: ``time,src,target,elevator_id,event_type`` where ``event_type`` is
``ALLOC`` (time = original call time), ``REALLOC`` (time = overload bump time) or ``STOP``
(time = car arrival at the floor; ``src`` = floor, ``target`` empty).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from src.models.dto import AllocationDecision, Request, StopRecord
from src.models.enums import RequestStatus
from src.simulation.observer import ISimulationObserver

OUTPUT_HEADER = ["time", "src", "target", "elevator_id", "event_type"]
SUMMARY_KEYS = [
    "simulation_name",
    "algorithm",
    "execution_mode",
    "total_requests",
    "completed_requests",
    "total_journey_time_sec",
    "avg_journey_time_sec",
    "avg_hall_wait_time_sec",
    "max_hall_wait_time_sec",
    "total_elevator_stops",
    "overload_events",
    "avg_dispatch_latency_ms",
]

Row = Tuple[float, int, Optional[int], int, str]


def build_summary(
    simulation_name: str,
    algorithm: str,
    execution_mode: str,
    requests: Iterable[Request],
    total_elevator_stops: int,
    overload_events: int,
    dispatch_latencies_ms: List[float],
) -> Dict[str, object]:
    """Compute the KPI dictionary (output spec §3.2). Averages are over completed requests."""
    reqs = list(requests)
    done = [r for r in reqs if r.status == RequestStatus.COMPLETED]
    journeys = [r.arrival_time - r.timestamp for r in done]  # type: ignore[operator]
    waits = [r.board_time - r.timestamp for r in done]  # type: ignore[operator]
    total_journey = sum(journeys)
    n = len(done)
    return {
        "simulation_name": simulation_name,
        "algorithm": algorithm,
        "execution_mode": execution_mode,
        "total_requests": len(reqs),
        "completed_requests": n,
        "total_journey_time_sec": round(total_journey, 2),
        "avg_journey_time_sec": round(total_journey / n, 2) if n else 0.0,
        "avg_hall_wait_time_sec": round(sum(waits) / n, 2) if n else 0.0,
        "max_hall_wait_time_sec": round(max(waits), 2) if n else 0.0,
        "total_elevator_stops": total_elevator_stops,
        "overload_events": overload_events,
        "avg_dispatch_latency_ms": (
            round(sum(dispatch_latencies_ms) / len(dispatch_latencies_ms), 3) if dispatch_latencies_ms else 0.0
        ),
    }


class SimulationLogger(ISimulationObserver):
    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = "outputs",
        csv_name: str = "simulation_output.csv",
        summary_name: str = "simulation_summary.json",
        auto_export: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.csv_name = csv_name
        self.summary_name = summary_name
        self.auto_export = auto_export
        self.summary: Optional[Dict[str, object]] = None
        self._rows: List[Tuple[float, int, Row]] = []  # (sort time, insertion seq, row)
        self._seen_req_ids: set[int] = set()
        self._bump_times: Dict[int, float] = {}

    # ------------------------------------------------------------------ observer hooks

    def on_request_allocated(self, request: Request, decision: AllocationDecision) -> None:
        if request.req_id in self._seen_req_ids:
            t = self._bump_times.pop(request.req_id, request.timestamp)
            event = "REALLOC"
        else:
            self._seen_req_ids.add(request.req_id)
            t = request.timestamp
            event = "ALLOC"
        self._append((t, request.src_floor, request.target_floor, decision.elevator_id, event))

    def on_elevator_stop(self, stop_record: StopRecord) -> None:
        self._append((stop_record.timestamp, stop_record.floor, None, stop_record.elevator_id, "STOP"))

    def on_overload_detected(self, elevator_id: int, bumped_request: Request, timestamp: float) -> None:
        self._bump_times[bumped_request.req_id] = timestamp

    def on_simulation_complete(self, summary_metrics: dict) -> None:
        self.summary = dict(summary_metrics)
        if self.auto_export and self.output_dir is not None:
            self.export()

    # ------------------------------------------------------------------ output

    def _append(self, row: Row) -> None:
        self._rows.append((row[0], len(self._rows), row))

    def rows(self) -> List[Row]:
        """All rows sorted chronologically (stable on emission order for equal times)."""
        return [r for _, _, r in sorted(self._rows, key=lambda x: (x[0], x[1]))]

    def to_csv_text(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(OUTPUT_HEADER)
        for t, src, target, eid, event in self.rows():
            w.writerow([f"{t:.2f}", src, "" if target is None else target, eid, event])
        return buf.getvalue()

    def export(self, output_dir: Optional[Union[str, Path]] = None) -> Tuple[Path, Optional[Path]]:
        out = Path(output_dir) if output_dir is not None else self.output_dir
        if out is None:
            raise ValueError("No output directory configured")
        out.mkdir(parents=True, exist_ok=True)
        csv_path = out / self.csv_name
        csv_path.write_text(self.to_csv_text(), encoding="utf-8")
        json_path = None
        if self.summary is not None:
            json_path = out / self.summary_name
            ordered = {k: self.summary[k] for k in SUMMARY_KEYS if k in self.summary}
            ordered.update({k: v for k, v in self.summary.items() if k not in ordered})
            json_path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
        return csv_path, json_path
