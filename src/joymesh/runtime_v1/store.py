"""In-memory + SQL-backed runtime persistence for tasks, leases, and audits."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from joymesh.models import utc_now
from joymesh.persistence import Base, Database
from joymesh.runtime_v1.models import (
    ExecutionAttempt,
    LeaseStatus,
    RouteCandidate,
    RuntimeAuditEvent,
    RuntimeTaskRecord,
    TaskLease,
    WorkspacePlacement,
)


class RuntimeTaskRow(Base):
    __tablename__ = "runtime_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    prompt_digest: Mapped[str] = mapped_column(String(128))
    prompt_size: Mapped[int] = mapped_column(Integer)
    requested_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    prohibited_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    expanded_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    policy_profile: Mapped[str] = mapped_column(String(80))
    preferred_connectors_json: Mapped[str] = mapped_column(Text, default="[]")
    required_connector: Mapped[str | None] = mapped_column(String(100))
    preferred_providers_json: Mapped[str] = mapped_column(Text, default="[]")
    required_provider: Mapped[str | None] = mapped_column(String(100))
    preferred_nodes_json: Mapped[str] = mapped_column(Text, default="[]")
    required_node: Mapped[str | None] = mapped_column(String(100))
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(40), index=True)
    selected_node_id: Mapped[str | None] = mapped_column(String(100))
    selected_connector_id: Mapped[str | None] = mapped_column(String(100))
    selected_backend_id: Mapped[str | None] = mapped_column(String(100))
    selected_harness_id: Mapped[str | None] = mapped_column(String(100))
    execution_id: Mapped[str | None] = mapped_column(String(80))
    execution_decision_reason: Mapped[str | None] = mapped_column(String(300))
    execution_fallback_order_json: Mapped[str] = mapped_column(Text, default="[]")
    provider_routing_required: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_provider_id: Mapped[str | None] = mapped_column(String(100))
    selected_provider_route_id: Mapped[str | None] = mapped_column(String(200))
    selected_provider_route_manager_id: Mapped[str | None] = mapped_column(String(100))
    selected_model_id: Mapped[str | None] = mapped_column(String(300))
    provider_selection_reason: Mapped[str | None] = mapped_column(String(200))
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RouteCandidateRow(Base):
    __tablename__ = "route_candidates"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "scheduling_round", "node_id", "connector_id", name="uq_route_candidate"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    scheduling_round: Mapped[int] = mapped_column(Integer, default=1)
    node_id: Mapped[str] = mapped_column(String(100))
    connector_id: Mapped[str] = mapped_column(String(100))
    policy_profile: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float, default=0)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    scoring_factors_json: Mapped[str] = mapped_column(Text, default="{}")
    certified_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskLeaseRow(Base):
    __tablename__ = "task_leases"
    __table_args__ = (
        UniqueConstraint("task_id", "active_marker", name="uq_active_lease_per_task"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    node_id: Mapped[str] = mapped_column(String(100))
    connector_id: Mapped[str] = mapped_column(String(100))
    attempt_id: Mapped[str] = mapped_column(String(36), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40))
    # Non-null only while lease is active, enabling one-active-lease uniqueness.
    active_marker: Mapped[str | None] = mapped_column(String(36))


class ExecutionAttemptRow(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_number", name="uq_attempt_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(100))
    connector_id: Mapped[str] = mapped_column(String(100))
    lease_id: Mapped[str] = mapped_column(String(36))
    execution_origin: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_class: Mapped[str | None] = mapped_column(String(40))
    retry_safe: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkspacePlacementRow(Base):
    __tablename__ = "workspace_placements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    node_id: Mapped[str] = mapped_column(String(100), index=True)
    local_path: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(128))
    writable: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expose_path: Mapped[bool] = mapped_column(Boolean, default=False)


class RuntimeAuditEventRow(Base):
    __tablename__ = "runtime_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CertifiedCapabilityRow(Base):
    __tablename__ = "certified_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id",
            "capability_id",
            name="uq_certified_capability_evidence",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(120), index=True)
    connector_id: Mapped[str] = mapped_column(String(100), index=True)
    node_id: Mapped[str] = mapped_column(String(100), index=True)
    certification_profile: Mapped[str] = mapped_column(String(80))
    certification_profile_revision: Mapped[str] = mapped_column(String(40))
    evidence_id: Mapped[str] = mapped_column(String(36), index=True)
    execution_origin: Mapped[str] = mapped_column(String(40))
    trust_level: Mapped[str] = mapped_column(String(40))
    executable_fingerprint: Mapped[str] = mapped_column(String(128))
    connector_revision: Mapped[str] = mapped_column(String(100))
    connector_version: Mapped[str] = mapped_column(String(300))
    capability_definition_revision: Mapped[str] = mapped_column(String(40))
    certified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    constraints_json: Mapped[str] = mapped_column(Text, default="{}")
    invalidation_reason: Mapped[str | None] = mapped_column(Text)


class RuntimeStore:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database
        self.tasks: dict[str, RuntimeTaskRecord] = {}
        self.candidates: dict[str, list[RouteCandidate]] = defaultdict(list)
        self.scheduling_rounds: dict[str, int] = defaultdict(int)
        self.leases: dict[str, TaskLease] = {}
        self.attempts: dict[str, list[ExecutionAttempt]] = defaultdict(list)
        self.placements: dict[str, list[WorkspacePlacement]] = defaultdict(list)
        self.audits: list[RuntimeAuditEvent] = []
        self.events: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def save_task(self, task: RuntimeTaskRecord) -> RuntimeTaskRecord:
        self.tasks[task.task_id] = task
        if self.database is not None:
            row = RuntimeTaskRow(
                id=task.task_id,
                workspace_id=task.workspace_id,
                user_id=task.user_id,
                prompt_digest=task.prompt_digest,
                prompt_size=task.prompt_size,
                requested_capabilities_json=json.dumps(list(task.requested_capabilities)),
                prohibited_capabilities_json=json.dumps(list(task.prohibited_capabilities)),
                expanded_capabilities_json=json.dumps(list(task.expanded_capabilities)),
                policy_profile=task.policy_profile,
                preferred_connectors_json=json.dumps(list(task.preferred_connectors)),
                required_connector=task.required_connector,
                preferred_providers_json=json.dumps(list(task.preferred_providers)),
                required_provider=task.required_provider,
                preferred_nodes_json=json.dumps(list(task.preferred_nodes)),
                required_node=task.required_node,
                timeout_seconds=task.timeout_seconds,
                max_attempts=task.max_attempts,
                status=task.status.value,
                selected_node_id=task.selected_node_id,
                selected_connector_id=task.selected_connector_id,
                selected_backend_id=task.selected_backend_id,
                selected_harness_id=task.selected_harness_id,
                execution_id=task.execution_id,
                execution_decision_reason=task.execution_decision_reason,
                execution_fallback_order_json=json.dumps(list(task.execution_fallback_order)),
                provider_routing_required=task.provider_routing_required,
                selected_provider_id=task.selected_provider_id,
                selected_provider_route_id=task.selected_provider_route_id,
                selected_provider_route_manager_id=task.selected_provider_route_manager_id,
                selected_model_id=task.selected_model_id,
                provider_selection_reason=task.provider_selection_reason,
                approval_required=task.approval_required,
                detail=task.detail,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            async with self.database.sessions() as session:
                existing = await session.get(RuntimeTaskRow, task.task_id)
                if existing is None:
                    session.add(row)
                else:
                    for key, value in row.__dict__.items():
                        if key.startswith("_"):
                            continue
                        setattr(existing, key, value)
                await session.commit()
        return task

    async def get_task(self, task_id: str) -> RuntimeTaskRecord:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown runtime task: {task_id}") from exc

    async def save_candidates(
        self, task_id: str, candidates: list[RouteCandidate]
    ) -> list[RouteCandidate]:
        self.scheduling_rounds[task_id] += 1
        round_id = self.scheduling_rounds[task_id]
        self.candidates[task_id] = list(candidates)
        if self.database is not None:
            async with self.database.sessions() as session:
                for candidate in candidates:
                    session.add(
                        RouteCandidateRow(
                            id=str(uuid4()),
                            task_id=task_id,
                            scheduling_round=round_id,
                            node_id=candidate.node_id,
                            connector_id=candidate.connector_id,
                            policy_profile=candidate.policy_profile,
                            score=candidate.score,
                            eligible=candidate.eligible,
                            rejection_reasons_json=json.dumps(list(candidate.rejection_reasons)),
                            scoring_factors_json=json.dumps(dict(candidate.scoring_factors)),
                            certified_capabilities_json=json.dumps(
                                sorted(candidate.certified_capabilities)
                            ),
                            created_at=utc_now(),
                        )
                    )
                await session.commit()
        return candidates

    async def save_lease(self, lease: TaskLease) -> TaskLease:
        self.leases[lease.task_id] = lease
        if self.database is not None:
            async with self.database.sessions() as session:
                # Clear other active markers for this task.
                rows = (
                    await session.scalars(
                        select(TaskLeaseRow).where(TaskLeaseRow.task_id == lease.task_id)
                    )
                ).all()
                for row in rows:
                    if row.active_marker is not None:
                        row.active_marker = None
                        row.status = LeaseStatus.RELEASED.value
                session.add(
                    TaskLeaseRow(
                        id=lease.lease_id,
                        task_id=lease.task_id,
                        node_id=lease.node_id,
                        connector_id=lease.connector_id,
                        attempt_id=lease.attempt_id,
                        fencing_token=lease.fencing_token,
                        acquired_at=lease.acquired_at,
                        expires_at=lease.expires_at,
                        heartbeat_at=lease.heartbeat_at,
                        status=lease.status.value,
                        active_marker=lease.task_id if lease.status is LeaseStatus.ACTIVE else None,
                    )
                )
                await session.commit()
        return lease

    async def save_attempt(self, attempt: ExecutionAttempt) -> ExecutionAttempt:
        attempts = self.attempts[attempt.task_id]
        updated = [item for item in attempts if item.attempt_id != attempt.attempt_id]
        updated.append(attempt)
        self.attempts[attempt.task_id] = sorted(updated, key=lambda item: item.attempt_number)
        if self.database is not None:
            async with self.database.sessions() as session:
                session.add(
                    ExecutionAttemptRow(
                        id=attempt.attempt_id,
                        task_id=attempt.task_id,
                        attempt_number=attempt.attempt_number,
                        node_id=attempt.node_id,
                        connector_id=attempt.connector_id,
                        lease_id=attempt.lease_id,
                        execution_origin=attempt.execution_origin.value,
                        status=attempt.status,
                        started_at=attempt.started_at,
                        terminal_at=attempt.terminal_at,
                        failure_class=attempt.failure_class.value
                        if attempt.failure_class
                        else None,
                        retry_safe=attempt.retry_safe,
                    )
                )
                await session.commit()
        return attempt

    async def save_placement(self, placement: WorkspacePlacement) -> WorkspacePlacement:
        items = [
            item
            for item in self.placements[placement.workspace_id]
            if item.node_id != placement.node_id
        ]
        items.append(placement)
        self.placements[placement.workspace_id] = items
        if self.database is not None:
            async with self.database.sessions() as session:
                session.add(
                    WorkspacePlacementRow(
                        id=str(uuid4()),
                        workspace_id=placement.workspace_id,
                        node_id=placement.node_id,
                        local_path=placement.local_path,
                        fingerprint=placement.fingerprint,
                        writable=placement.writable,
                        last_verified_at=placement.last_verified_at,
                        expose_path=placement.expose_path,
                    )
                )
                await session.commit()
        return placement

    async def audit(self, event_type: str, *, task_id: str | None, payload: dict[str, Any]) -> None:
        event = RuntimeAuditEvent(
            event_id=str(uuid4()),
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )
        self.audits.append(event)
        if self.database is not None:
            async with self.database.sessions() as session:
                session.add(
                    RuntimeAuditEventRow(
                        id=event.event_id,
                        task_id=task_id,
                        event_type=event_type,
                        payload_json=json.dumps(payload, sort_keys=True),
                        created_at=event.created_at,
                    )
                )
                await session.commit()

    def append_event(self, task_id: str, event: dict[str, Any]) -> dict[str, Any]:
        sequence = len(self.events[task_id]) + 1
        payload = {"sequence": sequence, **event}
        self.events[task_id].append(payload)
        return payload
