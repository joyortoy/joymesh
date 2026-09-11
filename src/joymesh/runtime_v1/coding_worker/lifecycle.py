"""Lease lifecycle helpers for the coding worker."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from joymesh.runtime_v1.leases import LeaseService
from joymesh.runtime_v1.models import LeaseStatus, TaskLease


class CodingWorkerLeaseError(PermissionError):
    pass


@dataclass
class CodingWorkerLeaseHandle:
    lease: TaskLease
    leases: LeaseService

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id

    @property
    def fencing_token(self) -> int:
        return self.lease.fencing_token

    def heartbeat(self) -> TaskLease:
        self.lease = self.leases.heartbeat(self.lease.task_id, self.lease.fencing_token)
        return self.lease

    def release(self) -> TaskLease:
        self.lease = self.leases.release(self.lease.task_id, self.lease.fencing_token)
        return self.lease


def acquire_exclusive_lease(
    leases: LeaseService,
    *,
    task_id: str,
    worker_id: str,
    connector_id: str = "codex",
    attempt_id: str | None = None,
) -> CodingWorkerLeaseHandle:
    current = leases.active_lease(task_id)
    if current is not None and current.status is LeaseStatus.ACTIVE:
        raise CodingWorkerLeaseError("duplicate task lease prevented")
    lease = leases.acquire(
        task_id=task_id,
        node_id=worker_id,
        connector_id=connector_id,
        attempt_id=attempt_id or f"coding_attempt_{uuid4().hex}",
    )
    return CodingWorkerLeaseHandle(lease=lease, leases=leases)


def recover_stale_lease(leases: LeaseService, task_id: str) -> bool:
    current = leases.active_lease(task_id)
    if current is None:
        return True
    if current.status is LeaseStatus.EXPIRED:
        return True
    if current.status is LeaseStatus.RELEASED:
        return True
    return False
