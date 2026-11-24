"""Allocation strategies behind the ``IDispatcher`` interface."""
from src.dispatchers.base import IDispatcher
from src.dispatchers.baselines import FCFSDispatcher, SCANDispatcher
from src.dispatchers.mc_ddi import MarginalCostDDDispatcher
from src.dispatchers.route_evaluator import RouteEvaluator, insert_or_merge

LOCAL_DISPATCHERS = {
    "FCFS": FCFSDispatcher,
    "SCAN": SCANDispatcher,
    "MC_DDI": MarginalCostDDDispatcher,
}


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
    "insert_or_merge",
]
