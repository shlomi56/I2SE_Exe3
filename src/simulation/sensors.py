"""Virtual cabin sensor suite (LLD §2.1)."""
from __future__ import annotations

from src.models.dto import SensorSnapshot


class SensorSuite:
    """Simulates the hardware sensors of one cabin: position/speed passthrough, load cell,
    vibration monitor and door-obstruction detector."""

    def __init__(self, max_weight_kg: float, vibration_threshold: float) -> None:
        self.max_weight_kg = max_weight_kg
        self.vibration_threshold = vibration_threshold
        self.door_obstructed = False
        self.vibration_level = 0.0
        self.fault_active = False

    def read_snapshot(
        self,
        elevator_id: int,
        timestamp: float,
        position: float,
        speed: float,
        true_cabin_weight: float,
        at_capacity: bool,
    ) -> SensorSnapshot:
        """Package the live state into an immutable telemetry frame."""
        return SensorSnapshot(
            elevator_id=elevator_id,
            timestamp=timestamp,
            position=position,
            speed=speed,
            current_weight_kg=true_cabin_weight,
            vibration_level=self.vibration_level,
            door_obstructed=self.door_obstructed,
            is_operational=self.is_safe_to_operate(),
            is_at_capacity=at_capacity,
        )

    def is_overloaded(self, true_cabin_weight: float) -> bool:
        return true_cabin_weight > self.max_weight_kg

    def is_safe_to_operate(self) -> bool:
        return not self.fault_active and self.vibration_level <= self.vibration_threshold

    def inject_door_obstruction(self, obstructed: bool) -> None:
        self.door_obstructed = obstructed

    def inject_vibration_fault(self, level: float) -> None:
        """Set the measured vibration level (0.0 clears the anomaly)."""
        self.vibration_level = level

    def set_fault_active(self, active: bool) -> None:
        """Hard fault flag (used for ``OUT_OF_SERVICE`` injected faults)."""
        self.fault_active = active
