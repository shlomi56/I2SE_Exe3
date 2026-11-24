"""``IDispatcher`` strategy interface (LLD §3.2).

``active_assignments`` maps ``elevator_id`` to an :class:`ElevatorAssignment` holding the car's current
``stop_queue`` and its active ``requests`` (onboard + assigned waiting) — Phase 1 audit item 5.

Route semantics shared by the engine and every dispatcher: a car serves ``stop_queue`` strictly in
order; a waiting request boards at the **first** stop at its ``src_floor`` and alights at the first
stop at its ``target_floor`` after boarding.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from src.models.dto import AllocationDecision, BuildingConfig, ElevatorAssignment, Request, SensorSnapshot


class IDispatcher(ABC):
    #: Reported as ``algorithm`` in simulation_summary.json.
    algorithm_name: str = "UNKNOWN"
    #: Reported as ``execution_mode`` ("LOCAL" or "ESP32").
    execution_mode: str = "LOCAL"

    @abstractmethod
    def initialize(self, building_config: BuildingConfig) -> None:
        """Receive static building and elevator specifications once at startup."""

    @abstractmethod
    def allocate(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> AllocationDecision:
        """Select the elevator and full updated stop route for ``request``."""

    def notify_state_update(self, fleet_snapshots: List[SensorSnapshot]) -> None:
        """Optional periodic telemetry sync (used by the ESP32 digital twin). Default: no-op."""

    def get_parking_targets(
        self,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
        current_time: float,
    ) -> Dict[int, int]:
        """Optional idle-parking hook: ``{elevator_id: floor}`` for idle cars to reposition. Default: none."""
        return {}
