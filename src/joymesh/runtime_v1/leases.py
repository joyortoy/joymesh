"""Task leases with fencing tokens."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.runtime_v1.models import LeaseStatus, TaskLease


class LeaseService:
    """In-memory lease manager with durable-store hooks via RuntimeStore."""

    def __init__(self, *, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._active: dict[str, TaskLease] = {}
        self._tokens: dict[str, int] = {}

    def active_lease(self, task_id: str) -> TaskLease | None:
        lease = self._active.get(task_id)
        if lease is None:
            return None
        if lease.expires_at <= utc_now():
            expired = TaskLease(
                lease_id=lease.lease_id,
                task_id=lease.task_id,
                node_id=lease.node_id,
                connector_id=lease.connector_id,
                attempt_id=lease.attempt_id,
                fencing_token=lease.fencing_token,
                acquired_at=lease.acquired_at,
                expires_at=lease.expires_at,
                heartbeat_at=lease.heartbeat_at,
                status=LeaseStatus.EXPIRED,
            )
            self._active[task_id] = expired
            return expired
        return lease

    def acquire(
        self,
        *,
        task_id: str,
        node_id: str,
        connector_id: str,
        attempt_id: str,
        now: datetime | None = None,
    ) -> TaskLease:
        current = self.active_lease(task_id)
        if current is not None and current.status is LeaseStatus.ACTIVE:
            raise PermissionError("task already has an active lease")
        now = now or utc_now()
        token = self._tokens.get(task_id, 0) + 1
        self._tokens[task_id] = token
        lease = TaskLease(
            lease_id=str(uuid4()),
            task_id=task_id,
            node_id=node_id,
            connector_id=connector_id,
            attempt_id=attempt_id,
            fencing_token=token,
            acquired_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            heartbeat_at=now,
            status=LeaseStatus.ACTIVE,
        )
        self._active[task_id] = lease
        return lease

    def heartbeat(self, task_id: str, fencing_token: int) -> TaskLease:
        lease = self.active_lease(task_id)
        if lease is None or lease.status is not LeaseStatus.ACTIVE:
            raise PermissionError("no active lease")
        if fencing_token != lease.fencing_token:
            raise PermissionError("stale fencing token")
        now = utc_now()
        renewed = TaskLease(
            lease_id=lease.lease_id,
            task_id=lease.task_id,
            node_id=lease.node_id,
            connector_id=lease.connector_id,
            attempt_id=lease.attempt_id,
            fencing_token=lease.fencing_token,
            acquired_at=lease.acquired_at,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            heartbeat_at=now,
            status=LeaseStatus.ACTIVE,
        )
        self._active[task_id] = renewed
        return renewed

    def release(self, task_id: str, fencing_token: int) -> TaskLease:
        lease = self.active_lease(task_id)
        if lease is None:
            raise PermissionError("no lease to release")
        if fencing_token != lease.fencing_token:
            raise PermissionError("stale fencing token")
        released = TaskLease(
            lease_id=lease.lease_id,
            task_id=lease.task_id,
            node_id=lease.node_id,
            connector_id=lease.connector_id,
            attempt_id=lease.attempt_id,
            fencing_token=lease.fencing_token,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            heartbeat_at=lease.heartbeat_at,
            status=LeaseStatus.RELEASED,
        )
        self._active[task_id] = released
        return released

    def validate_event(
        self,
        *,
        task_id: str,
        lease_id: str,
        fencing_token: int,
        attempt_id: str,
    ) -> TaskLease:
        lease = self.active_lease(task_id)
        if lease is None:
            raise PermissionError("no lease for task")
        if lease.lease_id != lease_id:
            raise PermissionError("lease_id mismatch")
        if lease.attempt_id != attempt_id:
            raise PermissionError("attempt_id mismatch")
        if fencing_token != lease.fencing_token:
            raise PermissionError("stale fencing token")
        if lease.status is not LeaseStatus.ACTIVE:
            raise PermissionError(f"lease is {lease.status.value}")
        return lease
