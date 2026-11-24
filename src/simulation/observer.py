"""Observer interface (LLD §4.2). Concrete observers override only the hooks they need."""
from __future__ import annotations

from abc import ABC
from typing import List

from src.models.dto import AllocationDecision, Request, SensorSnapshot, StopRecord


class ISimulationObserver(ABC):  # noqa: B024 — hooks are optional, defaults are no-ops
    def on_request_allocated(self, request: Request, decision: AllocationDecision) -> None:
        """Called after every ALLOC and REALLOC (a re-allocation re-uses ``request.req_id``)."""

    def on_elevator_stop(self, stop_record: StopRecord) -> None:
        """Called once per physical stop, when the doors finish opening."""

    def on_overload_detected(self, elevator_id: int, bumped_request: Request, timestamp: float) -> None:
        """Called right before the bumped request is re-dispatched."""

    def on_tick(self, current_time: float, fleet_snapshots: List[SensorSnapshot]) -> None:
        """Called at the end of every ``dt`` tick."""

    def on_simulation_complete(self, summary_metrics: dict) -> None:
        """Called once with the final KPI dictionary."""
