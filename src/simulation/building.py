"""Building aggregate: elevator fleet + floor waiting areas (LLD §2.3)."""
from __future__ import annotations

import random
from typing import Dict, List, Optional

from src.models.dto import BuildingConfig, ElevatorAssignment, Request, SensorSnapshot
from src.models.enums import RequestStatus
from src.simulation.elevator import Elevator
from src.simulation.sensors import SensorSuite

_WAITING_STATUSES = (RequestStatus.PENDING, RequestStatus.ASSIGNED, RequestStatus.BUMPED_OVERLOAD)


class Building:
    def __init__(self, config: BuildingConfig, seed: Optional[int] = None) -> None:
        self.config = config
        seed = config.simulation.random_seed if seed is None else seed
        self.elevators: Dict[int, Elevator] = {}
        for ecfg in sorted(config.elevators, key=lambda e: e.elevator_id):
            sensors = SensorSuite(ecfg.max_weight_kg, config.sensors.max_vibration_threshold)
            self.elevators[ecfg.elevator_id] = Elevator(
                ecfg,
                sensors,
                config.min_floor,
                config.max_floor,
                door_obstruction_probability=config.sensors.door_obstruction_probability,
                door_obstruction_delay_sec=config.sensors.door_obstruction_delay_sec,
                # independent, reproducible stream per car (str seeds are hashed deterministically)
                rng=random.Random(f"{seed}:door:{ecfg.elevator_id}"),
            )
        self.floor_queues: Dict[int, List[Request]] = {f: [] for f in config.floors}

    def get_elevator(self, elevator_id: int) -> Elevator:
        try:
            return self.elevators[elevator_id]
        except KeyError:
            raise KeyError(f"No elevator with id {elevator_id}") from None

    def get_fleet_telemetry(self, current_time: float) -> List[SensorSnapshot]:
        return [e.get_telemetry(current_time) for e in self.elevators.values()]

    def add_waiting_passenger(self, request: Request) -> None:
        queue = self.floor_queues[request.src_floor]
        if request not in queue:
            queue.append(request)

    def is_valid_floor(self, floor: int) -> bool:
        return self.config.min_floor <= floor <= self.config.max_floor

    # ------------------------------------------------------------------ extensions

    def get_active_assignments(self) -> Dict[int, ElevatorAssignment]:
        """Copy of every car's stop queue and active requests, for ``IDispatcher.allocate``."""
        return {
            eid: ElevatorAssignment(stop_queue=list(e.stop_queue), requests=e.active_requests)
            for eid, e in self.elevators.items()
        }

    def sync_floor_queues(self) -> None:
        """Drop passengers who have boarded (or completed) from the hall queues."""
        for floor, queue in self.floor_queues.items():
            if queue:
                self.floor_queues[floor] = [r for r in queue if r.status in _WAITING_STATUSES]
