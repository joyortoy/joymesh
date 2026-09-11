"""Database-backed provider-route mutation leases with process-local gating."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from joymesh.models import utc_now
from joymesh.persistence import Base, Database


class ProviderRouteLeaseRow(Base):
    """At most one active provider-route mutation lease per manager+connector."""

    __tablename__ = "provider_route_leases"
    __table_args__ = (
        UniqueConstraint(
            "manager_id",
            "connector_id",
            "active_marker",
            name="uq_active_provider_route_lease",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    manager_id: Mapped[str] = mapped_column(String(100), index=True)
    connector_id: Mapped[str] = mapped_column(String(100), index=True)
    owner_execution_id: Mapped[str] = mapped_column(String(100), index=True)
    lease_token: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    # Non-null only while active — enforces one active lease per manager+connector.
    active_marker: Mapped[str | None] = mapped_column(String(16))
    original_state_json: Mapped[str] = mapped_column(Text, default="{}")
    target_provider_id: Mapped[str | None] = mapped_column(String(100))
    target_model_id: Mapped[str | None] = mapped_column(String(300))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_status: Mapped[str | None] = mapped_column(String(40))
    details_json: Mapped[str] = mapped_column(Text, default="{}")


@dataclass(frozen=True)
class ProviderRouteLease:
    lease_id: str
    manager_id: str
    connector_id: str
    owner_execution_id: str
    lease_token: str
    status: str
    original_state: Mapping[str, Any]
    target_provider_id: str | None
    target_model_id: str | None
    acquired_at: datetime
    expires_at: datetime
    heartbeat_at: datetime
    recovery_status: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "manager_id": self.manager_id,
            "connector_id": self.connector_id,
            "owner_execution_id": self.owner_execution_id,
            "lease_token": self.lease_token,
            "status": self.status,
            "original_state": dict(self.original_state),
            "target_provider_id": self.target_provider_id,
            "target_model_id": self.target_model_id,
            "acquired_at": self.acquired_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "heartbeat_at": self.heartbeat_at.isoformat(),
            "recovery_status": self.recovery_status,
            "details": dict(self.details),
        }


class ProviderRouteLeaseError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class ProviderRouteLeaseStore:
    """Cross-process lease store (SQL) with optional in-memory fallback for tests."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database
        self._memory: dict[str, ProviderRouteLease] = {}
        self._memory_by_key: dict[tuple[str, str], str] = {}
        self._local_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._gate = asyncio.Lock()

    def local_lock(self, manager_id: str, connector_id: str) -> asyncio.Lock:
        key = (manager_id, connector_id)
        lock = self._local_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._local_locks[key] = lock
        return lock

    async def try_acquire(
        self,
        *,
        manager_id: str,
        connector_id: str,
        owner_execution_id: str,
        ttl_seconds: float,
        original_state: Mapping[str, Any],
        target_provider_id: str | None = None,
        target_model_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> ProviderRouteLease | None:
        now = utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        lease_id = str(uuid4())
        token = uuid4().hex
        lease = ProviderRouteLease(
            lease_id=lease_id,
            manager_id=manager_id,
            connector_id=connector_id,
            owner_execution_id=owner_execution_id,
            lease_token=token,
            status="active",
            original_state=dict(original_state),
            target_provider_id=target_provider_id,
            target_model_id=target_model_id,
            acquired_at=now,
            expires_at=expires,
            heartbeat_at=now,
            details=dict(details or {}),
        )
        if self.database is None:
            return await self._memory_acquire(lease)
        return await self._db_acquire(lease)

    async def renew(
        self,
        *,
        lease_id: str,
        lease_token: str,
        owner_execution_id: str,
        ttl_seconds: float,
    ) -> ProviderRouteLease:
        if self.database is None:
            return self._memory_renew(lease_id, lease_token, owner_execution_id, ttl_seconds)
        return await self._db_renew(lease_id, lease_token, owner_execution_id, ttl_seconds)

    async def release(
        self,
        *,
        lease_id: str,
        lease_token: str,
        owner_execution_id: str,
    ) -> ProviderRouteLease:
        if self.database is None:
            return self._memory_release(lease_id, lease_token, owner_execution_id)
        return await self._db_release(lease_id, lease_token, owner_execution_id)

    async def get(self, lease_id: str) -> ProviderRouteLease | None:
        if self.database is None:
            return self._memory.get(lease_id)
        async with self.database.sessions() as session:
            row = await session.get(ProviderRouteLeaseRow, lease_id)
            return None if row is None else _row_to_lease(row)

    async def get_active(self, manager_id: str, connector_id: str) -> ProviderRouteLease | None:
        if self.database is None:
            lease_id = self._memory_by_key.get((manager_id, connector_id))
            if not lease_id:
                return None
            lease = self._memory.get(lease_id)
            if lease is None or lease.status != "active":
                return None
            if lease.expires_at <= utc_now():
                return None
            return lease
        async with self.database.sessions() as session:
            result = await session.execute(
                select(ProviderRouteLeaseRow).where(
                    ProviderRouteLeaseRow.manager_id == manager_id,
                    ProviderRouteLeaseRow.connector_id == connector_id,
                    ProviderRouteLeaseRow.active_marker == "active",
                )
            )
            row = result.scalar_one_or_none()
            return None if row is None else _row_to_lease(row)

    async def list_expired_active(self, *, now: datetime | None = None) -> list[ProviderRouteLease]:
        moment = now or utc_now()
        if self.database is None:
            out: list[ProviderRouteLease] = []
            for lease in self._memory.values():
                if (
                    lease.status in {"active", "recovering"}
                    and lease.expires_at <= moment
                    and self._memory_by_key.get((lease.manager_id, lease.connector_id))
                    == lease.lease_id
                ):
                    out.append(lease)
            return out
        async with self.database.sessions() as session:
            result = await session.execute(
                select(ProviderRouteLeaseRow).where(
                    ProviderRouteLeaseRow.active_marker == "active",
                    ProviderRouteLeaseRow.status.in_(("active", "recovering")),
                    ProviderRouteLeaseRow.expires_at <= moment,
                )
            )
            return [_row_to_lease(row) for row in result.scalars().all()]

    async def claim_recovery(
        self,
        *,
        lease_id: str,
        lease_token: str,
        recovery_owner_id: str,
    ) -> ProviderRouteLease:
        """Transactionally claim exclusive recovery authority without a new lease.

        Keeps ``active_marker`` so normal acquire still blocks until recovery
        resolves the lease. Does not deadlock: recovery never calls try_acquire.
        """

        if self.database is None:
            return self._memory_claim_recovery(lease_id, lease_token, recovery_owner_id)
        return await self._db_claim_recovery(lease_id, lease_token, recovery_owner_id)

    async def mark_recovery(
        self,
        *,
        lease_id: str,
        lease_token: str,
        recovery_status: str,
        details: Mapping[str, Any] | None = None,
    ) -> ProviderRouteLease:
        if self.database is None:
            lease = self._memory.get(lease_id)
            if lease is None or lease.lease_token != lease_token:
                raise ProviderRouteLeaseError("stale_lease_token", "invalid lease token")
            clear_active = recovery_status.startswith("restored") or recovery_status in {
                "recovery_failed",
                "expired_no_restore",
            }
            updated = ProviderRouteLease(
                lease_id=lease.lease_id,
                manager_id=lease.manager_id,
                connector_id=lease.connector_id,
                owner_execution_id=lease.owner_execution_id,
                lease_token=lease.lease_token,
                status="expired" if clear_active else lease.status,
                original_state=lease.original_state,
                target_provider_id=lease.target_provider_id,
                target_model_id=lease.target_model_id,
                acquired_at=lease.acquired_at,
                expires_at=lease.expires_at,
                heartbeat_at=lease.heartbeat_at,
                recovery_status=recovery_status,
                details={**dict(lease.details), **dict(details or {})},
            )
            self._memory[lease_id] = updated
            if clear_active:
                self._memory_by_key.pop((lease.manager_id, lease.connector_id), None)
            return updated
        async with self.database.sessions() as session:
            row = await session.get(ProviderRouteLeaseRow, lease_id)
            if row is None or row.lease_token != lease_token:
                raise ProviderRouteLeaseError("stale_lease_token", "invalid lease token")
            row.recovery_status = recovery_status
            if details:
                current = json.loads(row.details_json or "{}")
                current.update(dict(details))
                row.details_json = json.dumps(current, sort_keys=True)
            if recovery_status.startswith("restored") or recovery_status in {
                "recovery_failed",
                "expired_no_restore",
            }:
                row.status = "expired"
                row.active_marker = None
                row.released_at = utc_now()
            await session.commit()
            return _row_to_lease(row)

    async def block_connector(
        self,
        *,
        manager_id: str,
        connector_id: str,
        reason: str,
    ) -> None:
        """Record a recovery-block flag in details of a sentinel released lease."""

        details = {"blocked": True, "reason": reason, "at": utc_now().isoformat()}
        if self.database is None:
            key = ("__block__", manager_id, connector_id)
            self._memory[f"block:{manager_id}:{connector_id}"] = ProviderRouteLease(
                lease_id=f"block:{manager_id}:{connector_id}",
                manager_id=manager_id,
                connector_id=connector_id,
                owner_execution_id="recovery",
                lease_token="block",
                status="blocked",
                original_state={},
                target_provider_id=None,
                target_model_id=None,
                acquired_at=utc_now(),
                expires_at=utc_now(),
                heartbeat_at=utc_now(),
                recovery_status="recovery_failed",
                details=details,
            )
            del key
            return
        # Persist as a non-active row for audit; blockers checked via is_blocked.
        async with self.database.sessions() as session:
            session.add(
                ProviderRouteLeaseRow(
                    id=str(uuid4()),
                    manager_id=manager_id,
                    connector_id=connector_id,
                    owner_execution_id="recovery",
                    lease_token=uuid4().hex,
                    status="blocked",
                    active_marker=None,
                    original_state_json="{}",
                    target_provider_id=None,
                    target_model_id=None,
                    acquired_at=utc_now(),
                    expires_at=utc_now(),
                    heartbeat_at=utc_now(),
                    released_at=utc_now(),
                    recovery_status="recovery_failed",
                    details_json=json.dumps(details, sort_keys=True),
                )
            )
            await session.commit()

    async def is_blocked(self, manager_id: str, connector_id: str) -> str | None:
        if self.database is None:
            lease = self._memory.get(f"block:{manager_id}:{connector_id}")
            if lease and lease.status == "blocked":
                return str(lease.details.get("reason") or "recovery_failed")
            return None
        async with self.database.sessions() as session:
            result = await session.execute(
                select(ProviderRouteLeaseRow)
                .where(
                    ProviderRouteLeaseRow.manager_id == manager_id,
                    ProviderRouteLeaseRow.connector_id == connector_id,
                    ProviderRouteLeaseRow.status == "blocked",
                )
                .order_by(ProviderRouteLeaseRow.acquired_at.desc())
            )
            row = result.scalars().first()
            if row is None:
                return None
            details = json.loads(row.details_json or "{}")
            return str(details.get("reason") or "recovery_failed")

    async def clear_block(self, manager_id: str, connector_id: str) -> None:
        if self.database is None:
            self._memory.pop(f"block:{manager_id}:{connector_id}", None)
            return
        async with self.database.sessions() as session:
            result = await session.execute(
                select(ProviderRouteLeaseRow).where(
                    ProviderRouteLeaseRow.manager_id == manager_id,
                    ProviderRouteLeaseRow.connector_id == connector_id,
                    ProviderRouteLeaseRow.status == "blocked",
                )
            )
            for row in result.scalars().all():
                row.status = "block_cleared"
            await session.commit()

    async def _memory_acquire(self, lease: ProviderRouteLease) -> ProviderRouteLease | None:
        async with self._gate:
            key = (lease.manager_id, lease.connector_id)
            existing_id = self._memory_by_key.get(key)
            if existing_id:
                existing = self._memory.get(existing_id)
                if existing and existing.status in {"active", "recovering"}:
                    return None
                self._memory_by_key.pop(key, None)
            self._memory[lease.lease_id] = lease
            self._memory_by_key[key] = lease.lease_id
            return lease

    def _memory_renew(
        self,
        lease_id: str,
        lease_token: str,
        owner_execution_id: str,
        ttl_seconds: float,
    ) -> ProviderRouteLease:
        lease = self._memory.get(lease_id)
        if lease is None:
            raise ProviderRouteLeaseError("lease_not_found", "lease not found")
        if lease.lease_token != lease_token or lease.owner_execution_id != owner_execution_id:
            raise ProviderRouteLeaseError("stale_lease_token", "lease token or owner mismatch")
        if lease.status != "active":
            raise ProviderRouteLeaseError("lease_not_active", f"lease status is {lease.status}")
        now = utc_now()
        if lease.expires_at <= now:
            raise ProviderRouteLeaseError("lease_expired", "lease expired")
        updated = ProviderRouteLease(
            lease_id=lease.lease_id,
            manager_id=lease.manager_id,
            connector_id=lease.connector_id,
            owner_execution_id=lease.owner_execution_id,
            lease_token=lease.lease_token,
            status=lease.status,
            original_state=lease.original_state,
            target_provider_id=lease.target_provider_id,
            target_model_id=lease.target_model_id,
            acquired_at=lease.acquired_at,
            expires_at=now + timedelta(seconds=ttl_seconds),
            heartbeat_at=now,
            recovery_status=lease.recovery_status,
            details=lease.details,
        )
        self._memory[lease_id] = updated
        return updated

    def _memory_release(
        self,
        lease_id: str,
        lease_token: str,
        owner_execution_id: str,
    ) -> ProviderRouteLease:
        lease = self._memory.get(lease_id)
        if lease is None:
            raise ProviderRouteLeaseError("lease_not_found", "lease not found")
        if lease.lease_token != lease_token or lease.owner_execution_id != owner_execution_id:
            raise ProviderRouteLeaseError("stale_lease_token", "lease token or owner mismatch")
        if lease.status != "active":
            raise ProviderRouteLeaseError("duplicate_release", "lease already released")
        updated = ProviderRouteLease(
            lease_id=lease.lease_id,
            manager_id=lease.manager_id,
            connector_id=lease.connector_id,
            owner_execution_id=lease.owner_execution_id,
            lease_token=lease.lease_token,
            status="released",
            original_state=lease.original_state,
            target_provider_id=lease.target_provider_id,
            target_model_id=lease.target_model_id,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            heartbeat_at=utc_now(),
            recovery_status=lease.recovery_status,
            details=lease.details,
        )
        self._memory[lease_id] = updated
        self._memory_by_key.pop((lease.manager_id, lease.connector_id), None)
        return updated

    def _memory_claim_recovery(
        self,
        lease_id: str,
        lease_token: str,
        recovery_owner_id: str,
    ) -> ProviderRouteLease:
        lease = self._memory.get(lease_id)
        if lease is None or lease.lease_token != lease_token:
            raise ProviderRouteLeaseError("stale_lease_token", "invalid lease token")
        if self._memory_by_key.get((lease.manager_id, lease.connector_id)) != lease_id:
            raise ProviderRouteLeaseError("lease_not_active", "lease is not the active marker")
        if lease.expires_at > utc_now() and lease.status == "active":
            raise ProviderRouteLeaseError("lease_not_expired", "lease has not expired")
        existing_owner = lease.details.get("recovery_owner_id")
        if lease.status == "recovering" and existing_owner not in {None, recovery_owner_id}:
            raise ProviderRouteLeaseError(
                "recovery_claimed",
                "recovery already claimed by another owner",
            )
        updated = ProviderRouteLease(
            lease_id=lease.lease_id,
            manager_id=lease.manager_id,
            connector_id=lease.connector_id,
            owner_execution_id=lease.owner_execution_id,
            lease_token=lease.lease_token,
            status="recovering",
            original_state=lease.original_state,
            target_provider_id=lease.target_provider_id,
            target_model_id=lease.target_model_id,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            heartbeat_at=utc_now(),
            recovery_status=lease.recovery_status,
            details={**dict(lease.details), "recovery_owner_id": recovery_owner_id},
        )
        self._memory[lease_id] = updated
        return updated

    async def _db_acquire(self, lease: ProviderRouteLease) -> ProviderRouteLease | None:
        assert self.database is not None
        async with self.database.sessions() as session:
            # Expire any stale active lease for this key first.
            result = await session.execute(
                select(ProviderRouteLeaseRow).where(
                    ProviderRouteLeaseRow.manager_id == lease.manager_id,
                    ProviderRouteLeaseRow.connector_id == lease.connector_id,
                    ProviderRouteLeaseRow.active_marker == "active",
                )
            )
            existing = result.scalar_one_or_none()
            # Never silently clear an active lease: expired leases require
            # coordinator recovery (restore saved original state) first.
            if existing is not None:
                return None
            session.add(
                ProviderRouteLeaseRow(
                    id=lease.lease_id,
                    manager_id=lease.manager_id,
                    connector_id=lease.connector_id,
                    owner_execution_id=lease.owner_execution_id,
                    lease_token=lease.lease_token,
                    status="active",
                    active_marker="active",
                    original_state_json=json.dumps(dict(lease.original_state), sort_keys=True),
                    target_provider_id=lease.target_provider_id,
                    target_model_id=lease.target_model_id,
                    acquired_at=lease.acquired_at,
                    expires_at=lease.expires_at,
                    heartbeat_at=lease.heartbeat_at,
                    released_at=None,
                    recovery_status=None,
                    details_json=json.dumps(dict(lease.details), sort_keys=True),
                )
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                return None
            return lease

    async def _db_renew(
        self,
        lease_id: str,
        lease_token: str,
        owner_execution_id: str,
        ttl_seconds: float,
    ) -> ProviderRouteLease:
        assert self.database is not None
        async with self.database.sessions() as session:
            row = await session.get(ProviderRouteLeaseRow, lease_id)
            if row is None:
                raise ProviderRouteLeaseError("lease_not_found", "lease not found")
            if row.lease_token != lease_token or row.owner_execution_id != owner_execution_id:
                raise ProviderRouteLeaseError("stale_lease_token", "lease token or owner mismatch")
            if row.status != "active" or row.active_marker != "active":
                raise ProviderRouteLeaseError("lease_not_active", f"lease status is {row.status}")
            now = utc_now()
            if _ensure_utc(row.expires_at) <= now:
                row.status = "expired"
                row.active_marker = None
                await session.commit()
                raise ProviderRouteLeaseError("lease_expired", "lease expired")
            row.expires_at = now + timedelta(seconds=ttl_seconds)
            row.heartbeat_at = now
            await session.commit()
            return _row_to_lease(row)

    async def _db_release(
        self,
        lease_id: str,
        lease_token: str,
        owner_execution_id: str,
    ) -> ProviderRouteLease:
        assert self.database is not None
        async with self.database.sessions() as session:
            row = await session.get(ProviderRouteLeaseRow, lease_id)
            if row is None:
                raise ProviderRouteLeaseError("lease_not_found", "lease not found")
            if row.lease_token != lease_token or row.owner_execution_id != owner_execution_id:
                raise ProviderRouteLeaseError("stale_lease_token", "lease token or owner mismatch")
            if row.status != "active" or row.active_marker is None:
                raise ProviderRouteLeaseError("duplicate_release", "lease already released")
            row.status = "released"
            row.active_marker = None
            row.released_at = utc_now()
            row.heartbeat_at = utc_now()
            await session.commit()
            return _row_to_lease(row)

    async def _db_claim_recovery(
        self,
        lease_id: str,
        lease_token: str,
        recovery_owner_id: str,
    ) -> ProviderRouteLease:
        assert self.database is not None
        async with self.database.sessions() as session:
            row = await session.get(ProviderRouteLeaseRow, lease_id)
            if row is None or row.lease_token != lease_token:
                raise ProviderRouteLeaseError("stale_lease_token", "invalid lease token")
            if row.active_marker != "active":
                raise ProviderRouteLeaseError("lease_not_active", "lease is not active")
            now = utc_now()
            if _ensure_utc(row.expires_at) > now and row.status == "active":
                raise ProviderRouteLeaseError("lease_not_expired", "lease has not expired")
            details = json.loads(row.details_json or "{}")
            existing_owner = details.get("recovery_owner_id")
            if row.status == "recovering" and existing_owner not in {None, recovery_owner_id}:
                raise ProviderRouteLeaseError(
                    "recovery_claimed",
                    "recovery already claimed by another owner",
                )
            details["recovery_owner_id"] = recovery_owner_id
            row.status = "recovering"
            row.details_json = json.dumps(details, sort_keys=True)
            row.heartbeat_at = now
            await session.commit()
            return _row_to_lease(row)


def _ensure_utc(value: datetime) -> datetime:
    """SQLite often returns naive datetimes; normalise for comparisons."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _row_to_lease(row: ProviderRouteLeaseRow) -> ProviderRouteLease:
    return ProviderRouteLease(
        lease_id=row.id,
        manager_id=row.manager_id,
        connector_id=row.connector_id,
        owner_execution_id=row.owner_execution_id,
        lease_token=row.lease_token,
        status=row.status,
        original_state=json.loads(row.original_state_json or "{}"),
        target_provider_id=row.target_provider_id,
        target_model_id=row.target_model_id,
        acquired_at=_ensure_utc(row.acquired_at),
        expires_at=_ensure_utc(row.expires_at),
        heartbeat_at=_ensure_utc(row.heartbeat_at),
        recovery_status=row.recovery_status,
        details=json.loads(row.details_json or "{}"),
    )


def sanitise_route_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only non-secret fields required for exact restoration."""

    allowed = (
        "connector_id",
        "provider_id",
        "enabled",
        "model_id",
        "configuration_status",
        "available",
        "authenticated",
    )
    return {key: snapshot.get(key) for key in allowed if key in snapshot}
