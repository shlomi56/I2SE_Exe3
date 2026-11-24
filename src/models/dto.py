"""Data transfer objects (LLD §1.2).

The classes and attribute names listed in the LLD are kept verbatim. Additions agreed during the
Phase 1 spec audit:

* ``ElevatorConfig.initial_floor`` — required to place the car at t = 0.
* ``BuildingConfig`` also carries every other section of ``simulation_config.json`` (audit item 6).
* ``SensorSnapshot.speed`` is **signed**: positive = moving up, negative = moving down (audit item 7).
* ``ElevatorAssignment`` — the per-elevator ``stop_queue`` + ``requests`` pair passed to
  ``IDispatcher.allocate`` as ``active_assignments`` (audit item 5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.models.enums import RequestStatus

# --------------------------------------------------------------------------------------------
# Static configuration
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ElevatorConfig:
    """Static physical and kinematic parameters for a single elevator car."""

    elevator_id: int
    max_speed: float  # floors / s
    start_time: float  # s, acceleration overhead per travel leg
    stop_time: float  # s, deceleration overhead per travel leg
    door_open_time: float  # s
    door_close_time: float  # s
    dwell_time: float  # s
    max_weight_kg: float
    max_capacity: int  # passengers
    initial_floor: int = 0

    @property
    def door_cycle_time(self) -> float:
        """Door open + dwell + door close overhead incurred at every stop."""
        return self.door_open_time + self.dwell_time + self.door_close_time


@dataclass(frozen=True)
class SimulationSettings:
    name: str
    duration_sec: float
    time_step_dt_sec: float
    random_seed: int
    realtime_scale: float


@dataclass(frozen=True)
class PassengerWeightConfig:
    """Ground-truth weight distribution (the mean lives on ``BuildingConfig.avg_passenger_weight_kg``)."""

    weight_std_kg: float
    min_weight_kg: float
    max_weight_kg: float


@dataclass(frozen=True)
class DispatcherSettings:
    algorithm: str  # "MC_DDI" | "SCAN" | "FCFS"
    soft_max_wait_sec: float
    fairness_weight: float
    enable_idle_parking: bool
    idle_parking_timeout_sec: float


@dataclass(frozen=True)
class InjectedFault:
    elevator_id: int
    type: str  # "VIBRATION_FAULT" | "OUT_OF_SERVICE"
    start_time_sec: float
    end_time_sec: float
    severity: float

    def is_active(self, t: float) -> bool:
        return self.start_time_sec <= t < self.end_time_sec


@dataclass(frozen=True)
class SensorSettings:
    max_vibration_threshold: float
    door_obstruction_probability: float
    door_obstruction_delay_sec: float
    injected_faults: List[InjectedFault] = field(default_factory=list)


@dataclass(frozen=True)
class TrafficPhase:
    name: str
    start_time_sec: float
    end_time_sec: float
    arrival_rate_per_min: float
    up_peak_ratio: float
    down_peak_ratio: float
    inter_floor_ratio: float

    @property
    def duration_sec(self) -> float:
        return self.end_time_sec - self.start_time_sec

    @property
    def rate_per_sec(self) -> float:
        return self.arrival_rate_per_min / 60.0


@dataclass(frozen=True)
class TrafficProfile:
    total_requests: Optional[int]  # None or 0 => open-ended Poisson (Mode 2)
    distribution: str  # "POISSON" | "UNIFORM"
    phases: List[TrafficPhase]


@dataclass(frozen=True)
class ESP32BridgeConfig:
    enabled: bool = False
    transport: str = "WIFI_TCP"
    host: str = "127.0.0.1"
    port: int = 5000
    ble_service_uuid: str = ""
    timeout_ms: int = 500


@dataclass(frozen=True)
class BuildingConfig:
    """Parsed directly from the standardized simulation JSON file.

    The first six fields are the LLD contract; the rest carry the remaining JSON sections.
    ``traffic_profile`` is the raw ``traffic_generator`` dict (LLD); ``traffic`` is its typed form.
    """

    min_floor: int
    max_floor: int
    elevators: List[ElevatorConfig]
    avg_passenger_weight_kg: float
    weight_buffer_ratio: float
    traffic_profile: dict
    lobby_floor: int
    floor_height_meters: float
    simulation: SimulationSettings
    passengers: PassengerWeightConfig
    dispatcher: DispatcherSettings
    sensors: SensorSettings
    traffic: TrafficProfile
    esp32_bridge: ESP32BridgeConfig = field(default_factory=ESP32BridgeConfig)
    schema_version: str = "1.0"

    @property
    def floors(self) -> List[int]:
        return list(range(self.min_floor, self.max_floor + 1))

    def get_elevator_config(self, elevator_id: int) -> ElevatorConfig:
        for cfg in self.elevators:
            if cfg.elevator_id == elevator_id:
                return cfg
        raise KeyError(f"No elevator with id {elevator_id}")


# --------------------------------------------------------------------------------------------
# Runtime objects
# --------------------------------------------------------------------------------------------


@dataclass
class Request:
    """A Destination Dispatch call ``(time, src, target)`` plus the hidden ground-truth weight."""

    req_id: int
    timestamp: float
    src_floor: int
    target_floor: int
    actual_weight_kg: float
    assigned_elevator_id: Optional[int] = None
    board_time: Optional[float] = None
    arrival_time: Optional[float] = None
    status: RequestStatus = RequestStatus.PENDING

    @property
    def wait_time(self) -> Optional[float]:
        """Hall waiting time W_i = T_board - t_i (None until boarded)."""
        return None if self.board_time is None else self.board_time - self.timestamp

    @property
    def journey_time(self) -> Optional[float]:
        """Total journey time J_i = T_arr - t_i (None until completed)."""
        return None if self.arrival_time is None else self.arrival_time - self.timestamp


@dataclass(frozen=True)
class SensorSnapshot:
    """Immutable telemetry frame read from a cabin's sensors. ``speed`` is signed (+ up / - down)."""

    elevator_id: int
    timestamp: float
    position: float
    speed: float
    current_weight_kg: float
    vibration_level: float
    door_obstructed: bool
    is_operational: bool
    is_at_capacity: bool


@dataclass
class AllocationDecision:
    """Output returned by any ``IDispatcher`` implementation."""

    req_id: int
    elevator_id: int
    planned_route: List[int]
    estimated_cost: float
    compute_time_ms: float = 0.0


@dataclass
class StopRecord:
    """Logged every time an elevator executes a physical stop."""

    timestamp: float
    elevator_id: int
    floor: int
    boarded_req_ids: List[int] = field(default_factory=list)
    alighted_req_ids: List[int] = field(default_factory=list)


@dataclass
class ElevatorAssignment:
    """Dispatcher-facing view of one elevator's commitments (``active_assignments[e_id]``)."""

    stop_queue: List[int] = field(default_factory=list)
    requests: List[Request] = field(default_factory=list)  # onboard + assigned waiting
