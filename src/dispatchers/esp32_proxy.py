"""``ESP32DispatcherProxy`` — Adapter/Proxy that runs MC-DDI on the ESP32 (LLD §3.3).

Each ``allocate`` serializes ``(request, fleet_snapshots, active_assignments)`` into one ``ALLOCATE_REQ``
line, sends it over an ``ITransport`` and decodes the ``ALLOCATE_RES``.

Link-failure policy (testing_strategy Layer 4 "link-drop resilience"):
  * a timeout, disconnect or malformed reply is logged and the call is retried after a reconnect
    (+ ``INIT_CONFIG`` re-sync), up to ``max_retries`` times;
  * if the edge still cannot answer (or replies ``ERROR``), the proxy fails safe by computing the
    decision with the identical local MC-DDI, so the simulation never hangs or drops a call.

Idle parking stays on the host (it is a fleet-repositioning hint, not an allocation decision).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.comms.protocol import (
    ProtocolError,
    decode_allocate_res,
    decode_message,
    encode_allocate_req,
    encode_init_config,
)
from src.comms.transport import ITransport, TransportError, TransportTimeout
from src.dispatchers.base import IDispatcher
from src.dispatchers.mc_ddi import MarginalCostDDDispatcher
from src.models.dto import AllocationDecision, BuildingConfig, ElevatorAssignment, Request, SensorSnapshot

log = logging.getLogger(__name__)


class ESP32DispatcherProxy(IDispatcher):
    algorithm_name = "MC_DDI"
    execution_mode = "ESP32"

    def __init__(
        self,
        transport: ITransport,
        address: str,
        port_or_uuid: str,
        timeout_ms: int = 500,
        max_retries: int = 1,
        fallback_to_local: bool = True,
    ) -> None:
        self.transport = transport
        self.address = address
        self.port_or_uuid = str(port_or_uuid)
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.fallback_to_local = fallback_to_local
        self.stats = {"sent": 0, "ok": 0, "timeouts": 0, "link_errors": 0, "edge_errors": 0, "reconnects": 0, "fallbacks": 0}
        self.last_error: Optional[str] = None
        self._local = MarginalCostDDDispatcher()
        self._init_payload: Optional[str] = None
        self._synced = False

    # ------------------------------------------------------------------ IDispatcher

    def initialize(self, building_config: BuildingConfig) -> None:
        self._local.initialize(building_config)
        self._init_payload = encode_init_config(building_config)
        if not self._ensure_link():
            log.warning("ESP32 at %s:%s unreachable at startup (%s); will retry per call",
                        self.address, self.port_or_uuid, self.last_error)

    def allocate(
        self,
        request: Request,
        fleet_snapshots: List[SensorSnapshot],
        active_assignments: Dict[int, ElevatorAssignment],
    ) -> AllocationDecision:
        self._local.record_call(request, fleet_snapshots)
        payload = encode_allocate_req(request, fleet_snapshots, active_assignments)

        for attempt in range(1 + self.max_retries):
            if not self._ensure_link():
                continue
            self.stats["sent"] += 1
            try:
                msg = decode_message(self.transport.send_and_receive(payload, self.timeout_ms))
            except TransportError as exc:
                key = "timeouts" if isinstance(exc, TransportTimeout) else "link_errors"
                self.stats[key] += 1
                self._drop_link(f"{type(exc).__name__}: {exc}", request, attempt)
                continue
            except ProtocolError as exc:
                self.stats["link_errors"] += 1
                self._drop_link(f"ProtocolError: {exc}", request, attempt)
                continue
            if msg["type"] == "ERROR":
                self.stats["edge_errors"] += 1
                self.last_error = f"edge ERROR {msg.get('code')}: {msg.get('message')}"
                log.warning("req %d: %s", request.req_id, self.last_error)
                break  # the edge answered deliberately; retrying the same input would fail again
            try:
                decision = decode_allocate_res(msg)
            except ProtocolError as exc:
                self.stats["link_errors"] += 1
                self._drop_link(str(exc), request, attempt)
                continue
            if decision.req_id != request.req_id:
                self.stats["link_errors"] += 1
                self._drop_link(f"reply for req {decision.req_id} while waiting for {request.req_id}", request, attempt)
                continue
            self.stats["ok"] += 1
            return decision

        if not self.fallback_to_local:
            raise TransportError(f"ESP32 allocation failed for req {request.req_id}: {self.last_error}")
        self.stats["fallbacks"] += 1
        log.warning("req %d: falling back to local MC-DDI (%s)", request.req_id, self.last_error)
        return self._local.compute_allocation(request, fleet_snapshots, active_assignments)

    def get_parking_targets(self, fleet_snapshots, active_assignments, current_time) -> Dict[int, int]:
        return self._local.get_parking_targets(fleet_snapshots, active_assignments, current_time)

    def close(self) -> None:
        self.transport.disconnect()
        self._synced = False

    # ------------------------------------------------------------------ link management

    def _ensure_link(self) -> bool:
        if self.transport.is_connected and self._synced:
            return True
        if not self.transport.is_connected:
            try:
                connected = self.transport.connect(self.address, self.port_or_uuid)
            except (OSError, NotImplementedError) as exc:
                connected = False
                log.warning("transport connect failed: %s", exc)
            if not connected:
                self.last_error = f"cannot connect to {self.address}:{self.port_or_uuid}"
                self.stats["link_errors"] += 1
                return False
            self.stats["reconnects"] += 1
        try:
            msg = decode_message(self.transport.send_and_receive(self._init_payload or "", self.timeout_ms))
        except (TransportError, ProtocolError) as exc:
            self.last_error = f"INIT_CONFIG failed: {exc}"
            self.transport.disconnect()
            return False
        if msg["type"] != "INIT_ACK":
            self.last_error = f"INIT_CONFIG rejected: {msg}"
            self.transport.disconnect()
            return False
        self._synced = True
        return True

    def _drop_link(self, reason: str, request: Request, attempt: int) -> None:
        self.last_error = reason
        log.warning("req %d attempt %d: %s", request.req_id, attempt + 1, reason)
        self.transport.disconnect()
        self._synced = False
