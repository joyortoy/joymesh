"""Durable onboarding progress repository.

Production uses SQL (`OnboardingProgressRow`). Tests may use the in-memory store.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from joymesh.control_plane.contracts import (
    OnboardingProgress,
    OnboardingState,
    PaidRoutePolicy,
)
from joymesh.control_plane.persistence import (
    OnboardingProgressRow,
    OrganisationRow,
    UserRow,
    WorkspaceRow,
)
from joymesh.models import utc_now


class OnboardingConflictError(RuntimeError):
    """Raised when a stale revision attempts to overwrite newer progress."""


class OnboardingProgressRepository(Protocol):
    async def get(
        self,
        *,
        user_id: str,
        workspace_id: str,
    ) -> OnboardingProgress | None: ...

    async def put(
        self,
        *,
        progress: OnboardingProgress,
        expected_revision: int | None = None,
    ) -> OnboardingProgress: ...


class InMemoryOnboardingProgressRepository:
    """Process-local repository for focused unit tests only."""

    def __init__(self) -> None:
        self._rows: dict[str, OnboardingProgress] = {}

    def _key(self, user_id: str, workspace_id: str) -> str:
        return f"{user_id}:{workspace_id}"

    async def get(self, *, user_id: str, workspace_id: str) -> OnboardingProgress | None:
        return self._rows.get(self._key(user_id, workspace_id))

    async def put(
        self,
        *,
        progress: OnboardingProgress,
        expected_revision: int | None = None,
    ) -> OnboardingProgress:
        key = self._key(progress.user_id, progress.workspace_id)
        current = self._rows.get(key)
        if expected_revision is not None and current is not None:
            if current.revision != expected_revision:
                raise OnboardingConflictError(
                    f"stale onboarding revision: expected {expected_revision}, "
                    f"found {current.revision}"
                )
        next_revision = 1 if current is None else current.revision + 1
        if expected_revision is None and current is not None:
            next_revision = current.revision + 1
        stored = progress.model_copy(
            update={
                "id": current.id if current is not None else progress.id,
                "revision": next_revision,
                "created_at": current.created_at if current is not None else progress.created_at,
                "updated_at": utc_now(),
            }
        )
        self._rows[key] = stored
        return stored


class SqlOnboardingProgressRepository:
    """SQL-backed production repository using existing onboarding_progress table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, *, user_id: str, workspace_id: str) -> OnboardingProgress | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(OnboardingProgressRow)
                .where(
                    OnboardingProgressRow.user_id == user_id,
                    OnboardingProgressRow.workspace_id == workspace_id,
                )
                .order_by(OnboardingProgressRow.updated_at.desc())
            )
            if row is None:
                return None
            return _from_row(row)

    async def put(
        self,
        *,
        progress: OnboardingProgress,
        expected_revision: int | None = None,
    ) -> OnboardingProgress:
        async with self.sessions() as session:
            await _ensure_identity_stubs(
                session,
                user_id=progress.user_id,
                organisation_id=progress.organisation_id,
                workspace_id=progress.workspace_id,
            )
            row = await session.scalar(
                select(OnboardingProgressRow).where(
                    OnboardingProgressRow.user_id == progress.user_id,
                    OnboardingProgressRow.workspace_id == progress.workspace_id,
                )
            )
            current = _from_row(row) if row is not None else None
            if expected_revision is not None and current is not None:
                if current.revision != expected_revision:
                    raise OnboardingConflictError(
                        f"stale onboarding revision: expected {expected_revision}, "
                        f"found {current.revision}"
                    )
            next_revision = 1 if current is None else current.revision + 1
            now = utc_now()
            payload = _to_data_json(
                progress.model_copy(
                    update={
                        "id": current.id if current is not None else progress.id,
                        "revision": next_revision,
                        "created_at": (
                            current.created_at if current is not None else progress.created_at
                        ),
                        "updated_at": now,
                    }
                )
            )
            # node_id column has an FK to nodes; keep null until node rows are durable.
            # Authoritative node_id remains in data_json.
            if row is None:
                row = OnboardingProgressRow(
                    id=progress.id,
                    user_id=progress.user_id,
                    workspace_id=progress.workspace_id,
                    node_id=None,
                    state=progress.state.value,
                    data_json=payload,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.state = progress.state.value
                row.data_json = payload
                row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return _from_row(row)


def _to_data_json(progress: OnboardingProgress) -> str:
    return json.dumps(
        {
            "organisation_id": progress.organisation_id,
            "node_id": progress.node_id,
            "pairing_id": progress.pairing_id,
            "selected_harnesses": list(progress.selected_harnesses),
            "completed_steps": [item.value for item in progress.completed_steps],
            "limited_mode_reason": progress.limited_mode_reason,
            "paid_route_policy": progress.paid_route_policy.value,
            "fireconnect_enabled": progress.fireconnect_enabled,
            "last_error": progress.last_error,
            "revision": progress.revision,
            "created_at": progress.created_at.isoformat(),
            "unsynchronised": progress.unsynchronised,
        }
    )


def _from_row(row: OnboardingProgressRow) -> OnboardingProgress:
    data = json.loads(row.data_json or "{}")
    created_raw = data.get("created_at")
    created_at = (
        datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
        if created_raw
        else row.updated_at
    )
    completed = tuple(
        OnboardingState(item)
        for item in data.get("completed_steps") or ()
        if item in OnboardingState
    )
    policy_raw = data.get("paid_route_policy") or PaidRoutePolicy.ASK.value
    try:
        policy = PaidRoutePolicy(policy_raw)
    except ValueError:
        policy = PaidRoutePolicy.ASK
    return OnboardingProgress(
        id=row.id,
        user_id=row.user_id,
        organisation_id=str(data.get("organisation_id") or ""),
        workspace_id=row.workspace_id,
        node_id=data.get("node_id") if isinstance(data.get("node_id"), str) else None,
        pairing_id=data.get("pairing_id") if isinstance(data.get("pairing_id"), str) else None,
        state=OnboardingState(row.state),
        selected_harnesses=tuple(data.get("selected_harnesses") or ()),
        completed_steps=completed,
        limited_mode_reason=data.get("limited_mode_reason"),
        paid_route_policy=policy,
        fireconnect_enabled=bool(data.get("fireconnect_enabled")),
        last_error=data.get("last_error"),
        revision=int(data.get("revision") or 1),
        created_at=created_at,
        updated_at=row.updated_at,
        unsynchronised=bool(data.get("unsynchronised")),
    )


async def _ensure_identity_stubs(
    session: AsyncSession,
    *,
    user_id: str,
    organisation_id: str,
    workspace_id: str,
) -> None:
    """Create minimal identity rows so onboarding FK constraints can be satisfied."""

    now = utc_now()
    org_id = organisation_id or f"org-{workspace_id}"
    if await session.get(OrganisationRow, org_id) is None:
        session.add(OrganisationRow(id=org_id, name="Onboarding organisation", created_at=now))
    if await session.get(UserRow, user_id) is None:
        session.add(
            UserRow(
                id=user_id,
                email=f"{user_id}@onboarding.local",
                display_name=user_id,
                created_at=now,
            )
        )
    if await session.get(WorkspaceRow, workspace_id) is None:
        session.add(
            WorkspaceRow(
                id=workspace_id,
                organisation_id=org_id,
                name="Onboarding workspace",
                created_at=now,
            )
        )
    await session.flush()


def new_progress(
    *,
    user_id: str,
    organisation_id: str,
    workspace_id: str,
) -> OnboardingProgress:
    now = utc_now()
    return OnboardingProgress(
        id=str(uuid4()),
        user_id=user_id,
        organisation_id=organisation_id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )
