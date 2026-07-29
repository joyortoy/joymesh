"""Capability-first JoyMesh Runtime service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from joymesh.connectors.lifecycle_models import ConnectorExecutionOrigin
from joymesh.models import utc_now
from joymesh.runtime_v1.capabilities import expand_capabilities
from joymesh.runtime_v1.cursor import CursorConnectorRuntime
from joymesh.runtime_v1.leases import LeaseService
from joymesh.runtime_v1.models import (
    CreateRuntimeTaskBody,
    ExecutionAttempt,
    FailureClass,
    RuntimeTaskRecord,
    RuntimeTaskRequest,
    RuntimeTaskStatus,
    WorkspacePlacement,
)
from joymesh.runtime_v1.policy import PolicyEngine
from joymesh.runtime_v1.retry import decide_retry
from joymesh.runtime_v1.scheduler import (
    RuntimeScheduler,
    SchedulerConnectorSnapshot,
    SchedulerNodeSnapshot,
)
from joymesh.runtime_v1.store import RuntimeStore


@dataclass
class RuntimeMetrics:
    connected_nodes: int = 0
    healthy_nodes: int = 0
    queued_tasks: int = 0
    leased_tasks: int = 0
    running_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    policy_rejections: int = 0
    stale_event_rejections: int = 0
    retry_count: int = 0
    failover_count: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "connected_nodes": self.connected_nodes,
            "healthy_nodes": self.healthy_nodes,
            "queued_tasks": self.queued_tasks,
            "leased_tasks": self.leased_tasks,
            "running_tasks": self.running_tasks,
            "successful_tasks": self.successful_tasks,
            "failed_tasks": self.failed_tasks,
            "cancelled_tasks": self.cancelled_tasks,
            "policy_rejections": self.policy_rejections,
            "stale_event_rejections": self.stale_event_rejections,
            "retry_count": self.retry_count,
            "failover_count": self.failover_count,
        }


@dataclass
class RuntimeService:
    store: RuntimeStore = field(default_factory=RuntimeStore)
    policy: PolicyEngine = field(default_factory=PolicyEngine)
    scheduler: RuntimeScheduler = field(default_factory=RuntimeScheduler)
    leases: LeaseService = field(default_factory=LeaseService)
    connectors: dict[str, Any] = field(default_factory=dict)
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    _nodes: dict[str, SchedulerNodeSnapshot] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if "cursor" not in self.connectors:
            self.connectors["cursor"] = CursorConnectorRuntime()
        self.scheduler = RuntimeScheduler(self.policy)

    def register_node(self, snapshot: SchedulerNodeSnapshot) -> None:
        self._nodes[snapshot.node_id] = snapshot
        self.metrics.connected_nodes = sum(1 for item in self._nodes.values() if item.online)
        self.metrics.healthy_nodes = sum(
            1
            for item in self._nodes.values()
            if item.online and not item.revoked and item.session_authenticated
        )

    async def create_task(self, body: CreateRuntimeTaskBody, *, user_id: str) -> RuntimeTaskRecord:
        request = RuntimeTaskRequest(
            workspace_id=body.workspace_id,
            prompt=body.prompt,
            requested_capabilities=frozenset(body.requested_capabilities),
            prohibited_capabilities=frozenset(body.prohibited_capabilities),
            policy_profile=body.policy_profile,
            preferred_connectors=body.preferred_connectors,
            required_connector=body.required_connector,
            preferred_nodes=body.preferred_nodes,
            required_node=body.required_node,
            timeout_seconds=body.timeout_seconds,
            max_attempts=body.max_attempts,
            user_id=user_id,
        )
        await self.store.audit(
            "task.created",
            task_id=request.task_id,
            payload={
                "prompt_digest": request.prompt_digest,
                "prompt_size": len(request.prompt),
                "policy_profile": request.policy_profile,
                "requested_capabilities": sorted(request.requested_capabilities),
                "prohibited_capabilities": sorted(request.prohibited_capabilities),
            },
        )
        decision = self.policy.evaluate(request)
        await self.store.audit(
            "policy.evaluated",
            task_id=request.task_id,
            payload={
                "allowed": decision.allowed,
                "reasons": list(decision.reasons),
                "granted": sorted(decision.granted_capabilities),
                "denied": sorted(decision.denied_capabilities),
                "approvals": list(decision.approval_requirements),
            },
        )
        if not decision.allowed:
            self.metrics.policy_rejections += 1
            task = RuntimeTaskRecord(
                task_id=request.task_id,
                workspace_id=request.workspace_id,
                user_id=user_id,
                prompt_digest=request.prompt_digest,
                prompt_size=len(request.prompt),
                requested_capabilities=tuple(sorted(request.requested_capabilities)),
                prohibited_capabilities=tuple(sorted(request.prohibited_capabilities)),
                expanded_capabilities=(),
                policy_profile=request.policy_profile,
                preferred_connectors=request.preferred_connectors,
                required_connector=request.required_connector,
                preferred_nodes=request.preferred_nodes,
                required_node=request.required_node,
                timeout_seconds=request.timeout_seconds,
                max_attempts=request.max_attempts,
                status=RuntimeTaskStatus.REJECTED,
                detail="; ".join(decision.reasons),
            )
            await self.store.save_task(task)
            return task

        expanded = expand_capabilities(
            request.requested_capabilities,
            prohibited=request.prohibited_capabilities,
        )
        await self.store.audit(
            "capabilities.expanded",
            task_id=request.task_id,
            payload={"expanded": sorted(expanded)},
        )
        approval_required = bool(decision.approval_requirements)
        task = RuntimeTaskRecord(
            task_id=request.task_id,
            workspace_id=request.workspace_id,
            user_id=user_id,
            prompt_digest=request.prompt_digest,
            prompt_size=len(request.prompt),
            requested_capabilities=tuple(sorted(request.requested_capabilities)),
            prohibited_capabilities=tuple(sorted(request.prohibited_capabilities)),
            expanded_capabilities=tuple(sorted(expanded)),
            policy_profile=request.policy_profile,
            preferred_connectors=request.preferred_connectors,
            required_connector=request.required_connector,
            preferred_nodes=request.preferred_nodes,
            required_node=request.required_node,
            timeout_seconds=request.timeout_seconds,
            max_attempts=request.max_attempts,
            status=(
                RuntimeTaskStatus.APPROVAL_REQUIRED
                if approval_required
                else RuntimeTaskStatus.ROUTING
            ),
            approval_required=approval_required,
        )
        await self.store.save_task(task)
        if approval_required:
            return task
        return await self.route_task(task.task_id)

    async def approve_task(self, task_id: str) -> RuntimeTaskRecord:
        task = await self.store.get_task(task_id)
        if task.status is not RuntimeTaskStatus.APPROVAL_REQUIRED:
            raise PermissionError("task is not awaiting approval")
        updated = task.model_copy(
            update={
                "status": RuntimeTaskStatus.ROUTING,
                "approval_required": False,
                "updated_at": utc_now(),
            }
        )
        await self.store.save_task(updated)
        await self.store.audit("task.approved", task_id=task_id, payload={})
        return await self.route_task(task_id)

    async def route_task(self, task_id: str) -> RuntimeTaskRecord:
        task = await self.store.get_task(task_id)
        request = _request_from_record(task)
        candidates = self.scheduler.rank_candidates(request, list(self._nodes.values()))
        await self.store.save_candidates(task_id, candidates)
        await self.store.audit(
            "scheduler.ranked",
            task_id=task_id,
            payload={
                "candidates": [
                    {
                        "node_id": item.node_id,
                        "connector_id": item.connector_id,
                        "eligible": item.eligible,
                        "score": item.score,
                        "rejection_reasons": list(item.rejection_reasons),
                        "scoring_factors": dict(item.scoring_factors),
                    }
                    for item in candidates
                ]
            },
        )
        eligible = next((item for item in candidates if item.eligible), None)
        if eligible is None:
            reasons = candidates[0].rejection_reasons if candidates else ("no candidates",)
            if any("offline" in reason for reason in reasons):
                queued = task.model_copy(
                    update={
                        "status": RuntimeTaskStatus.QUEUED,
                        "detail": "; ".join(reasons),
                        "updated_at": utc_now(),
                    }
                )
                self.metrics.queued_tasks += 1
                await self.store.save_task(queued)
                await self.store.audit(
                    "task.queued",
                    task_id=task_id,
                    payload={"reasons": list(reasons)},
                )
                return queued
            rejected = task.model_copy(
                update={
                    "status": RuntimeTaskStatus.REJECTED,
                    "detail": "; ".join(reasons),
                    "updated_at": utc_now(),
                }
            )
            await self.store.save_task(rejected)
            return rejected

        attempt_id = str(uuid4())
        attempt_number = len(self.store.attempts.get(task_id, [])) + 1
        lease = self.leases.acquire(
            task_id=task_id,
            node_id=eligible.node_id,
            connector_id=eligible.connector_id,
            attempt_id=attempt_id,
        )
        await self.store.save_lease(lease)
        attempt = ExecutionAttempt(
            attempt_id=attempt_id,
            task_id=task_id,
            attempt_number=attempt_number,
            node_id=eligible.node_id,
            connector_id=eligible.connector_id,
            lease_id=lease.lease_id,
            execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
            status="leased",
            started_at=None,
            terminal_at=None,
            failure_class=None,
            retry_safe=True,
        )
        await self.store.save_attempt(attempt)
        await self.store.audit(
            "lease.acquired",
            task_id=task_id,
            payload={
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "node_id": lease.node_id,
                "connector_id": lease.connector_id,
                "attempt_id": attempt_id,
            },
        )
        leased = task.model_copy(
            update={
                "status": RuntimeTaskStatus.LEASED,
                "selected_node_id": eligible.node_id,
                "selected_connector_id": eligible.connector_id,
                "updated_at": utc_now(),
                "detail": None,
            }
        )
        self.metrics.leased_tasks += 1
        await self.store.save_task(leased)
        self.store.append_event(
            task_id,
            {
                "event_type": "route.selected",
                "node_id": eligible.node_id,
                "connector_id": eligible.connector_id,
                "score": eligible.score,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "attempt_id": attempt_id,
            },
        )
        return leased

    async def mark_offered(self, task_id: str, fencing_token: int) -> RuntimeTaskRecord:
        lease = self.leases.active_lease(task_id)
        if lease is None or lease.fencing_token != fencing_token:
            self.metrics.stale_event_rejections += 1
            await self.store.audit(
                "event.stale_rejected",
                task_id=task_id,
                payload={"fencing_token": fencing_token},
            )
            raise PermissionError("stale fencing token")
        task = await self.store.get_task(task_id)
        updated = task.model_copy(
            update={"status": RuntimeTaskStatus.OFFERED, "updated_at": utc_now()}
        )
        await self.store.save_task(updated)
        await self.store.audit(
            "task.offered",
            task_id=task_id,
            payload={"lease_id": lease.lease_id},
        )
        return updated

    async def ingest_node_event(
        self,
        *,
        task_id: str,
        attempt_id: str,
        lease_id: str,
        fencing_token: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> RuntimeTaskRecord:
        try:
            self.leases.validate_event(
                task_id=task_id,
                lease_id=lease_id,
                fencing_token=fencing_token,
                attempt_id=attempt_id,
            )
        except PermissionError:
            self.metrics.stale_event_rejections += 1
            await self.store.audit(
                "event.stale_rejected",
                task_id=task_id,
                payload={
                    "event_type": event_type,
                    "fencing_token": fencing_token,
                    "lease_id": lease_id,
                    "attempt_id": attempt_id,
                },
            )
            raise
        task = await self.store.get_task(task_id)
        self.store.append_event(
            task_id,
            {"event_type": event_type, "payload": dict(payload), "attempt_id": attempt_id},
        )
        status_map = {
            "task.accepted": RuntimeTaskStatus.ACCEPTED,
            "task.started": RuntimeTaskStatus.RUNNING,
            "task.succeeded": RuntimeTaskStatus.SUCCEEDED,
            "task.failed": RuntimeTaskStatus.FAILED,
            "task.cancelled": RuntimeTaskStatus.CANCELLED,
        }
        if event_type not in status_map:
            return task
        new_status = status_map[event_type]
        updated = task.model_copy(
            update={"status": new_status, "updated_at": utc_now()}
        )
        await self.store.save_task(updated)
        if new_status is RuntimeTaskStatus.RUNNING:
            self.metrics.running_tasks += 1
        if new_status in {
            RuntimeTaskStatus.SUCCEEDED,
            RuntimeTaskStatus.FAILED,
            RuntimeTaskStatus.CANCELLED,
        }:
            self.leases.release(task_id, fencing_token)
            if new_status is RuntimeTaskStatus.SUCCEEDED:
                self.metrics.successful_tasks += 1
            elif new_status is RuntimeTaskStatus.FAILED:
                self.metrics.failed_tasks += 1
            else:
                self.metrics.cancelled_tasks += 1
            await self.store.audit(
                "task.terminal",
                task_id=task_id,
                payload={"status": new_status.value, "event_type": event_type},
            )
        return updated

    async def cancel_task(self, task_id: str) -> RuntimeTaskRecord:
        task = await self.store.get_task(task_id)
        if task.status in {
            RuntimeTaskStatus.SUCCEEDED,
            RuntimeTaskStatus.FAILED,
            RuntimeTaskStatus.CANCELLED,
            RuntimeTaskStatus.REJECTED,
        }:
            return task
        lease = self.leases.active_lease(task_id)
        if lease is not None and lease.status.value == "active":
            self.leases.release(task_id, lease.fencing_token)
        cancelled = task.model_copy(
            update={
                "status": RuntimeTaskStatus.CANCELLED,
                "updated_at": utc_now(),
                "detail": "user_cancelled",
            }
        )
        self.metrics.cancelled_tasks += 1
        await self.store.save_task(cancelled)
        await self.store.audit(
            "task.cancelled",
            task_id=task_id,
            payload={"failure_class": FailureClass.USER_CANCELLED.value},
        )
        return cancelled

    async def retry_task(self, task_id: str, *, failure_class: FailureClass) -> RuntimeTaskRecord:
        task = await self.store.get_task(task_id)
        attempts = self.store.attempts.get(task_id, [])
        decision = decide_retry(
            task=task,
            failure_class=failure_class,
            attempt_number=len(attempts),
            execution_started=task.status
            in {RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.ACCEPTED},
        )
        await self.store.audit(
            "retry.decided",
            task_id=task_id,
            payload={"retry": decision.retry, "reason": decision.reason},
        )
        if not decision.retry:
            raise PermissionError(decision.reason)
        self.metrics.retry_count += 1
        self.metrics.failover_count += 1
        reset = task.model_copy(
            update={"status": RuntimeTaskStatus.ROUTING, "updated_at": utc_now(), "detail": None}
        )
        await self.store.save_task(reset)
        return await self.route_task(task_id)

    async def register_placement(self, placement: WorkspacePlacement) -> WorkspacePlacement:
        return await self.store.save_placement(placement)

    def list_capabilities(self) -> list[dict[str, Any]]:
        from joymesh.runtime_v1.capabilities import CapabilityRegistry

        return [
            {
                "capability_id": item.capability_id,
                "description": item.description,
                "risk_level": item.risk_level,
                "dependencies": sorted(item.dependencies),
                "conflicts": sorted(item.conflicts),
                "default_approval_class": item.default_approval_class,
                "routable": item.routable,
                "experimental": item.experimental,
                "definition_revision": item.definition_revision,
            }
            for item in CapabilityRegistry().all()
        ]

    def list_policies(self) -> list[dict[str, Any]]:
        return [
            {
                "profile_id": item.profile_id,
                "description": item.description,
                "allowed": sorted(item.allowed),
                "denied": sorted(item.denied),
                "require_node_attested": item.require_node_attested,
                "enabled": item.enabled,
            }
            for item in self.policy.list_profiles()
        ]

    def health(self) -> dict[str, Any]:
        return {
            "control_plane": "ok",
            "scheduler": "ok",
            "lease_service": "ok",
            "active_node_sessions": self.metrics.connected_nodes,
            "task_queue": self.metrics.queued_tasks,
            "metrics": self.metrics.snapshot(),
        }


def _request_from_record(task: RuntimeTaskRecord) -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        task_id=task.task_id,
        workspace_id=task.workspace_id,
        prompt="",  # prompt is not rehydrated into audits by default
        requested_capabilities=frozenset(task.requested_capabilities),
        prohibited_capabilities=frozenset(task.prohibited_capabilities),
        policy_profile=task.policy_profile,
        preferred_connectors=task.preferred_connectors,
        required_connector=task.required_connector,
        preferred_nodes=task.preferred_nodes,
        required_node=task.required_node,
        timeout_seconds=task.timeout_seconds,
        max_attempts=task.max_attempts,
        user_id=task.user_id,
        created_at=task.created_at,
    )


def build_ready_cursor_node(
    *,
    node_id: str,
    workspace_id: str,
    local_path: str = "/tmp/workspace",
    online: bool = True,
    revoked: bool = False,
) -> SchedulerNodeSnapshot:
    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        EvidenceTrustLevel,
        NodeConnectorState,
    )
    from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES

    return SchedulerNodeSnapshot(
        node_id=node_id,
        online=online,
        revoked=revoked,
        session_authenticated=online and not revoked,
        connectors={
            "cursor": SchedulerConnectorSnapshot(
                connector_id="cursor",
                installed=True,
                readiness=NodeConnectorState.READY,
                authenticated=True,
                routing_enabled=True,
                certified_capabilities=READ_ONLY_CAPABILITIES,
                trust_level=EvidenceTrustLevel.NODE_ATTESTED,
                execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
            )
        },
        placements=(
            WorkspacePlacement(
                workspace_id=workspace_id,
                node_id=node_id,
                local_path=local_path,
                fingerprint="fp",
                writable=False,
                last_verified_at=utc_now(),
            ),
        ),
    )
