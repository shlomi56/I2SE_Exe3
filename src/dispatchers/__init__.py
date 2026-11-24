"""Allocation strategies behind the ``IDispatcher`` interface."""
from src.dispatchers.base import IDispatcher
from src.dispatchers.baselines import FCFSDispatcher, SCANDispatcher
from src.dispatchers.mc_ddi import MarginalCostDDDispatcher
from src.dispatchers.route_evaluator import RouteEvaluator, insert_or_merge
from src.models.dto import BuildingConfig

LOCAL_DISPATCHERS = {
    "FCFS": FCFSDispatcher,
    "SCAN": SCANDispatcher,
    "MC_DDI": MarginalCostDDDispatcher,
}


def create_dispatcher_for_config(config: BuildingConfig) -> IDispatcher:
    """``esp32_bridge.enabled`` selects the ESP32 proxy; otherwise ``dispatcher.algorithm`` runs locally."""
    bridge = config.esp32_bridge
    if not bridge.enabled:
        return create_dispatcher(config.dispatcher.algorithm)
    from src.comms.transport import BLEGattTransport, WiFiTCPTransport
    from src.dispatchers.esp32_proxy import ESP32DispatcherProxy

    if bridge.transport == "BLE_GATT":
        return ESP32DispatcherProxy(BLEGattTransport(), bridge.ble_service_uuid, bridge.ble_service_uuid, bridge.timeout_ms)
    return ESP32DispatcherProxy(WiFiTCPTransport(), bridge.host, str(bridge.port), bridge.timeout_ms)


def create_dispatcher(algorithm: str) -> IDispatcher:
    """Instantiate a local dispatcher by its config name (``"MC_DDI"``, ``"SCAN"``, ``"FCFS"``)."""
    try:
        return LOCAL_DISPATCHERS[algorithm]()
    except KeyError:
        raise ValueError(f"Unknown algorithm {algorithm!r}; expected one of {sorted(LOCAL_DISPATCHERS)}") from None


__all__ = [
    "FCFSDispatcher",
    "IDispatcher",
    "LOCAL_DISPATCHERS",
    "MarginalCostDDDispatcher",
    "RouteEvaluator",
    "SCANDispatcher",
    "create_dispatcher",
    "create_dispatcher_for_config",
    "insert_or_merge",
]
