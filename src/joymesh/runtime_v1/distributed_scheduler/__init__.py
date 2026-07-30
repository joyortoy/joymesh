"""Deprecated package — JoyMesh no longer owns distributed fleet scheduling.

Fleet scheduling, placement, fairness, queues, and scheduler HA live in JoyCLI.
JoyMesh retains neutral worker reporting and lease validation under
``joymesh.runtime_v1.workers`` and ``joymesh.runtime_v1.contracts``.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "joymesh.runtime_v1.distributed_scheduler is removed. "
    "JoyCLI owns distributed fleet scheduling. "
    "Use joymesh.runtime_v1.contracts / joymesh.runtime_v1.workers for neutral runtime facts.",
    DeprecationWarning,
    stacklevel=2,
)

# Compatibility redirects to neutral contracts only — no control-plane behaviour.
from joymesh.runtime_v1.contracts.workers import (  # noqa: E402
    WorkerCapacityReport,
    WorkerHeartbeat,
    WorkerReport,
)
from joymesh.runtime_v1.workers import (  # noqa: E402
    WorkerLeaseValidator,
    build_worker_heartbeat,
    build_worker_report,
)

__all__ = [
    "WorkerCapacityReport",
    "WorkerHeartbeat",
    "WorkerLeaseValidator",
    "WorkerReport",
    "build_worker_heartbeat",
    "build_worker_report",
]


def __getattr__(name: str) -> object:
    removed = {
        "DistributedScheduler",
        "ExecutionQueue",
        "PlacementEngine",
        "PlacementDecision",
        "PlacementFailure",
        "PlacementRequest",
        "FairnessController",
        "FairnessShare",
        "SchedulerHACoordinator",
        "HeartbeatProcessor",
        "WorkerRecord",
        "WorkerLease",
        "WorkerLeaseManager",
        "WorkerRegistry",
        "FleetSchedulerStore",
        "ResourceVector",
        "worker_from_node_snapshot",
    }
    if name in removed:
        raise ImportError(
            f"{name} was removed from JoyMesh. "
            "JoyCLI owns fleet scheduling, placement, fairness, queues, and lease granting."
        )
    raise AttributeError(name)
