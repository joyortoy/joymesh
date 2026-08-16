"""Capability-first JoyMesh Runtime service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from joymesh.connectors.lifecycle_models import ConnectorExecutionOrigin
from joymesh.models import utc_now
from joymesh.runtime_v1.capabilities import expand_capabilities
from joymesh.runtime_v1.completion import (
    CandidateEvidence,
    CompletionContext,
    CompletionFailureClass,
    CompletionLifecycleState,
    ExecutionCompletionOrchestrator,
)
from joymesh.runtime_v1.connectors import builtin_connectors
from joymesh.runtime_v1.contracts.workers import WorkerReport
from joymesh.runtime_v1.execution_routing import (
    BackendRegistry,
    ExecutionPlanner,
    ExecutionRouter,
    ExecutionRoutingService,
    ExecutionStatus,
)
from joymesh.runtime_v1.execution_routing.backends.joymesh import JoyMeshBackend
from joymesh.runtime_v1.execution_routing.bridge import mission_spec_from_task
from joymesh.runtime_v1.execution_routing.cancellation import CancellationRegistry
from joymesh.runtime_v1.execution_routing.models import ExecutionDecision, ExecutionIntent
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
from joymesh.runtime_v1.workers import build_worker_report


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
    tasks_by_connector: dict[str, int] = field(default_factory=dict)
    tasks_by_provider: dict[str, int] = field(default_factory=dict)
    tasks_by_provider_route: dict[str, int] = field(default_factory=dict)
    tasks_by_model: dict[str, int] = field(default_factory=dict)
    tasks_by_backend: dict[str, int] = field(default_factory=dict)
    provider_route_failures: int = 0
    provider_route_switches: int = 0

    def snapshot(self) -> dict[str, object]:
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
            "tasks_by_connector": dict(self.tasks_by_connector),
            "tasks_by_provider": dict(self.tasks_by_provider),
            "tasks_by_provider_route": dict(self.tasks_by_provider_route),
            "tasks_by_model": dict(self.tasks_by_model),
            "tasks_by_backend": dict(self.tasks_by_backend),
            "provider_route_failures": self.provider_route_failures,
            "provider_route_switches": self.provider_route_switches,
        }


@dataclass
class RuntimeService:
    store: RuntimeStore = field(default_factory=RuntimeStore)
    policy: PolicyEngine = field(default_factory=PolicyEngine)
    scheduler: RuntimeScheduler = field(default_factory=RuntimeScheduler)
    leases: LeaseService = field(default_factory=LeaseService)
    connectors: dict[str, Any] = field(default_factory=dict)
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    execution_routing: ExecutionRoutingService | None = None
    completion: ExecutionCompletionOrchestrator | None = None
    cancellation: CancellationRegistry = field(default_factory=CancellationRegistry)
    _nodes: dict[str, SchedulerNodeSnapshot] = field(default_factory=dict)
    _task_prompts: dict[str, str] = field(default_factory=dict)
    _authoritative_attempts: dict[str, str] = field(default_factory=dict)
    _worker_reports: dict[str, WorkerReport] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.connectors:
            self.connectors = builtin_connectors()
        self.scheduler = RuntimeScheduler(self.policy)
        if self.execution_routing is None:
            registry = BackendRegistry()
            joymesh = registry.get("joymesh")
            if isinstance(joymesh, JoyMeshBackend):
                joymesh._submit_fn = self._submit_remote_execution
            self.execution_routing = ExecutionRoutingService(
                registry=registry,
                planner=ExecutionPlanner(),
                router=ExecutionRouter(registry, cancellation=self.cancellation),
            )
        if self.completion is None:
            self.completion = ExecutionCompletionOrchestrator()

    def register_node(self, snapshot: SchedulerNodeSnapshot) -> None:
        self._nodes[snapshot.node_id] = snapshot
        self.metrics.connected_nodes = sum(1 for item in self._nodes.values() if item.online)
        self.metrics.healthy_nodes = sum(
            1
            for item in self._nodes.values()
            if item.online and not item.revoked and item.session_authenticated
        )
        # Publish a neutral worker report only — JoyMesh does not own fleet placement.
        self._worker_reports[snapshot.node_id] = self.build_worker_report(snapshot)

    def build_worker_report(self, snapshot: SchedulerNodeSnapshot) -> WorkerReport:
        return build_worker_report(snapshot)

    def publish_worker_report(self, snapshot: SchedulerNodeSnapshot) -> WorkerReport:
        report = self.build_worker_report(snapshot)
        self._worker_reports[snapshot.node_id] = report
        return report

    def latest_worker_report(self, worker_id: str) -> WorkerReport | None:
        return self._worker_reports.get(worker_id)

    async def create_task(
        self, body: CreateRuntimeTaskBody, *, user_id: str, skip_routing: bool = False
    ) -> RuntimeTaskRecord:
        request = RuntimeTaskRequest(
            workspace_id=body.workspace_id,
            prompt=body.prompt,
            requested_capabilities=frozenset(body.requested_capabilities),
            prohibited_capabilities=frozenset(body.prohibited_capabilities),
            policy_profile=body.policy_profile,
            preferred_connectors=body.preferred_connectors,
            required_connector=body.required_connector,
            preferred_providers=body.preferred_providers,
            required_provider=body.required_provider,
            preferred_nodes=body.preferred_nodes,
            required_node=body.required_node,
            timeout_seconds=body.timeout_seconds,
            max_attempts=body.max_attempts,
            user_id=user_id,
        )
        self._task_prompts[request.task_id] = body.prompt
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
                preferred_providers=request.preferred_providers,
                required_provider=request.required_provider,
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
            preferred_providers=request.preferred_providers,
            required_provider=request.required_provider,
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
        if skip_routing:
            # For JoyCLI compat: queue task without blocking on routing
            queued = task.model_copy(
                update={
                    "status": RuntimeTaskStatus.QUEUED,
                    "detail": "Queued for routing when workers available",
                    "updated_at": utc_now(),
                }
            )
            await self.store.save_task(queued)
            await self.store.audit(
                "task.queued",
                task_id=task.task_id,
                payload={"reason": "skip_routing_requested"},
            )
            self.store.append_event(
                task.task_id,
                {
                    "event_type": "accepted",
                    "status": "queued",
                    "detail": "Task accepted and queued",
                },
            )
            self.metrics.queued_tasks += 1
            return queued
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
        """Authoritative path: Planner → Router → Backend (node lease via JoyMeshBackend)."""

        assert self.execution_routing is not None
        task = await self.store.get_task(task_id)
        prompt = self._task_prompts.get(task_id, "")
        workspace_path = self._resolve_workspace_path(task.workspace_id)
        has_nodes = bool(self._nodes)
        locality = "remote" if has_nodes or task.required_node else "local"
        mission = mission_spec_from_task(
            task,
            prompt=prompt,
            workspace_path=workspace_path,
            workspace_ref=task.workspace_id,
            locality_preference=locality,
            requires_remote_worker=locality == "remote",
        )
        intent = self.execution_routing.plan(mission)
        await self.store.audit(
            "execution.planned",
            task_id=task_id,
            payload={
                "execution_id": intent.execution_id,
                "required_capabilities": sorted(c.value for c in intent.required_capabilities),
                "preferred_harness": intent.preferred_harness,
                "requires_provider_route": intent.requires_provider_route,
            },
        )
        try:
            decision = self.execution_routing.router.select(intent)
        except Exception as exc:
            rejected = task.model_copy(
                update={
                    "status": RuntimeTaskStatus.REJECTED,
                    "detail": str(exc),
                    "updated_at": utc_now(),
                    "execution_id": intent.execution_id,
                }
            )
            await self.store.save_task(rejected)
            await self.store.audit(
                "execution.blocked",
                task_id=task_id,
                payload={"execution_id": intent.execution_id, "error": str(exc)[:200]},
            )
            return rejected

        task = task.model_copy(
            update={
                "execution_id": decision.execution_id,
                "selected_backend_id": decision.selected_backend_id,
                "selected_harness_id": decision.selected_harness_id,
                "selected_connector_id": decision.selected_connector_id
                or decision.selected_harness_id,
                "execution_decision_reason": decision.reason,
                "execution_fallback_order": tuple(decision.fallback_order),
                "provider_routing_required": decision.provider_routing_required,
                "updated_at": utc_now(),
            }
        )
        await self.store.save_task(task)
        await self.store.audit(
            "backend.selected",
            task_id=task_id,
            payload=decision.as_dict(),
        )
        self.metrics.tasks_by_backend[decision.selected_backend_id] = (
            self.metrics.tasks_by_backend.get(decision.selected_backend_id, 0) + 1
        )
        self.store.append_event(
            task_id,
            {
                "event_type": "backend.selected",
                "execution_id": decision.execution_id,
                "backend_id": decision.selected_backend_id,
                "harness_id": decision.selected_harness_id,
                "reason": decision.reason,
                "fallback_order": list(decision.fallback_order),
            },
        )

        # Annotate preferred provider metadata without mutating FireConnect.
        if decision.provider_routing_required:
            task = task.model_copy(
                update={
                    "selected_provider_id": "fireworks",
                    "selected_provider_route_manager_id": "fireconnect",
                    "provider_selection_reason": "provider_routing_required_via_execution_router",
                }
            )
            await self.store.save_task(task)

        result = await self.execution_routing.router.execute_with_fallback(
            intent, decision=decision
        )
        for audit in result.audits:
            await self.store.audit(
                str(audit.get("event_type") or "backend.event"),
                task_id=task_id,
                payload=dict(audit),
            )
            self.store.append_event(task_id, dict(audit))

        if decision.selected_backend_id == "joymesh" and result.ok:
            # Remote lease already persisted by _submit_remote_execution.
            leased = await self.store.get_task(task_id)
            self.metrics.leased_tasks += 1
            if leased.selected_connector_id:
                self.metrics.tasks_by_connector[leased.selected_connector_id] = (
                    self.metrics.tasks_by_connector.get(leased.selected_connector_id, 0) + 1
                )
            return leased

        # Authoritative completion — backends never mark missions complete.
        assert self.completion is not None
        attempt_id = (
            result.attempts[-1]["attempt_id"]
            if result.attempts
            else f"execution_attempt_{task.execution_id or task_id}"
        )
        self._authoritative_attempts[result.execution_id] = attempt_id
        outcome = await self.completion.complete_from_backend(
            result,
            context=CompletionContext(
                organisation_id=None,
                project_id=task.workspace_id,
                mission_id=task.task_id,
                execution_id=result.execution_id,
                attempt_id=attempt_id,
                authoritative_attempt_id=attempt_id,
                backend_id=result.backend_id,
                harness_id=result.harness_id,
                correlation_id=task.task_id,
                user_id=task.user_id,
                verification_strategy="backend_success_with_evidence",
            ),
            decision=decision.as_dict(),
        )
        for event in outcome.events:
            await self.store.audit(
                str(event.get("event_type") or "completion.event"),
                task_id=task_id,
                payload=dict(event),
            )
            self.store.append_event(task_id, dict(event))

        if outcome.ok and outcome.state is CompletionLifecycleState.COMPLETED:
            succeeded = task.model_copy(
                update={
                    "status": RuntimeTaskStatus.SUCCEEDED,
                    "selected_backend_id": result.backend_id,
                    "selected_harness_id": result.harness_id,
                    "selected_connector_id": result.harness_id,
                    "detail": outcome.detail,
                    "updated_at": utc_now(),
                }
            )
            self.metrics.successful_tasks += 1
            await self.store.save_task(succeeded)
            return succeeded

        if outcome.state is CompletionLifecycleState.BLOCKED or (
            result.status is ExecutionStatus.BLOCKED
            or (not result.ok and "offline" in (result.message or ""))
        ):
            submission = dict(result.output.get("submission") or {})
            if submission.get("status") == "queued" or "offline" in (result.message or ""):
                status = RuntimeTaskStatus.QUEUED
            else:
                status = RuntimeTaskStatus.REJECTED
            blocked = task.model_copy(
                update={
                    "status": status,
                    "detail": outcome.detail or result.message,
                    "updated_at": utc_now(),
                }
            )
            if blocked.status is RuntimeTaskStatus.QUEUED:
                self.metrics.queued_tasks += 1
            await self.store.save_task(blocked)
            return blocked

        submission = dict(result.output.get("submission") or {})
        if submission.get("status") == "rejected":
            rejected = task.model_copy(
                update={
                    "status": RuntimeTaskStatus.REJECTED,
                    "detail": outcome.detail or result.message,
                    "updated_at": utc_now(),
                }
            )
            await self.store.save_task(rejected)
            return rejected

        failed = task.model_copy(
            update={
                "status": RuntimeTaskStatus.FAILED,
                "detail": outcome.detail or result.message,
                "updated_at": utc_now(),
                "selected_backend_id": result.backend_id,
                "selected_harness_id": result.harness_id,
            }
        )
        self.metrics.failed_tasks += 1
        await self.store.save_task(failed)
        return failed

    async def _submit_remote_execution(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]:
        """Map JoyMeshBackend.submit onto the existing node lease scheduler."""

        task = await self.store.get_task(intent.mission_id)
        request = _request_from_record(task)
        # Prefer router harness, but only require it when the mission explicitly required one.
        preferred = request.preferred_connectors
        if decision.selected_harness_id and decision.selected_harness_id not in preferred:
            preferred = (decision.selected_harness_id, *preferred)
        request = RuntimeTaskRequest(
            task_id=request.task_id,
            workspace_id=request.workspace_id,
            prompt=self._task_prompts.get(task.task_id, ""),
            requested_capabilities=request.requested_capabilities,
            prohibited_capabilities=request.prohibited_capabilities,
            policy_profile=request.policy_profile,
            preferred_connectors=preferred,
            required_connector=request.required_connector,
            preferred_providers=request.preferred_providers,
            required_provider=request.required_provider,
            preferred_nodes=request.preferred_nodes,
            required_node=request.required_node,
            timeout_seconds=request.timeout_seconds,
            max_attempts=request.max_attempts,
            user_id=request.user_id,
            created_at=request.created_at,
        )
        candidates = self.scheduler.rank_candidates(request, list(self._nodes.values()))
        await self.store.save_candidates(task.task_id, candidates)
        await self.store.audit(
            "scheduler.ranked",
            task_id=task.task_id,
            payload={
                "execution_id": intent.execution_id,
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
                ],
            },
        )
        eligible = next((item for item in candidates if item.eligible), None)
        if eligible is None:
            reasons = candidates[0].rejection_reasons if candidates else ("no workers available",)
            detail = "; ".join(reasons)
            # Always queue when no eligible candidates - workers may come online later
            queued = task.model_copy(
                update={
                    "status": RuntimeTaskStatus.QUEUED,
                    "detail": detail,
                    "updated_at": utc_now(),
                    "execution_id": intent.execution_id,
                    "selected_backend_id": decision.selected_backend_id,
                    "selected_harness_id": decision.selected_harness_id,
                }
            )
            self.metrics.queued_tasks += 1
            await self.store.save_task(queued)
            return {"status": "queued", "message": detail, "ok": False}

        attempt_id = f"execution_attempt_{uuid4().hex}"
        attempt_number = len(self.store.attempts.get(task.task_id, [])) + 1
        lease = self.leases.acquire(
            task_id=task.task_id,
            node_id=eligible.node_id,
            connector_id=eligible.connector_id,
            attempt_id=attempt_id,
        )
        await self.store.save_lease(lease)
        attempt = ExecutionAttempt(
            attempt_id=attempt_id,
            task_id=task.task_id,
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
        self._authoritative_attempts[intent.execution_id] = attempt_id
        await self.store.audit(
            "lease.acquired",
            task_id=task.task_id,
            payload={
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "node_id": lease.node_id,
                "connector_id": lease.connector_id,
                "attempt_id": attempt_id,
                "execution_id": intent.execution_id,
            },
        )
        leased = task.model_copy(
            update={
                "status": RuntimeTaskStatus.LEASED,
                "selected_node_id": eligible.node_id,
                "selected_connector_id": eligible.connector_id,
                "selected_harness_id": eligible.connector_id,
                "selected_backend_id": decision.selected_backend_id,
                "execution_id": intent.execution_id,
                "execution_decision_reason": decision.reason,
                "execution_fallback_order": tuple(decision.fallback_order),
                "provider_routing_required": decision.provider_routing_required,
                "updated_at": utc_now(),
                "detail": None,
            }
        )
        await self.store.save_task(leased)
        self.store.append_event(
            task.task_id,
            {
                "event_type": "route.selected",
                "execution_id": intent.execution_id,
                "backend_id": decision.selected_backend_id,
                "node_id": eligible.node_id,
                "connector_id": eligible.connector_id,
                "harness_id": decision.selected_harness_id,
                "score": eligible.score,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "attempt_id": attempt_id,
            },
        )
        return {
            "status": "leased",
            "ok": True,
            "execution_id": intent.execution_id,
            "attempt_id": attempt_id,
            "lease_id": lease.lease_id,
            "node_id": eligible.node_id,
            "harness_id": decision.selected_harness_id,
            "fencing_token": lease.fencing_token,
        }

    def _resolve_workspace_path(self, workspace_id: str) -> str:
        for placements in self.store.placements.values():
            for placement in placements:
                if placement.workspace_id == workspace_id:
                    return placement.local_path
        for node in self._nodes.values():
            for placement in node.placements:
                if placement.workspace_id == workspace_id:
                    return placement.local_path
        # Safe placeholder for local-only routing before placement registration.
        return str(Path("/tmp") / "joymesh-workspaces" / workspace_id)

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
        if event_type in {"task.accepted"}:
            updated = task.model_copy(
                update={"status": RuntimeTaskStatus.ACCEPTED, "updated_at": utc_now()}
            )
            await self.store.save_task(updated)
            return updated
        if event_type in {"task.started"}:
            updated = task.model_copy(
                update={"status": RuntimeTaskStatus.RUNNING, "updated_at": utc_now()}
            )
            self.metrics.running_tasks += 1
            await self.store.save_task(updated)
            return updated

        if event_type not in {
            "task.succeeded",
            "task.failed",
            "task.cancelled",
            "verification.completed",
            "execution.completed",
            "execution.failed",
            "execution.cancelled",
        }:
            return task

        assert self.completion is not None
        execution_id = task.execution_id or task_id
        authoritative = self._authoritative_attempts.get(execution_id, attempt_id)
        candidates: list[CandidateEvidence] = []
        verification_payload = payload.get("verification")
        if isinstance(verification_payload, Mapping):
            candidates.append(
                CandidateEvidence(
                    evidence_type="remote_verification",
                    sequence=int(payload.get("sequence") or 1),
                    provenance={
                        "execution_id": execution_id,
                        "attempt_id": attempt_id,
                        "mission_id": task.task_id,
                        "project_id": task.workspace_id,
                    },
                    payload=dict(verification_payload),
                )
            )
        elif event_type == "task.succeeded" and payload.get("verified") is True:
            candidates.append(
                CandidateEvidence(
                    evidence_type="remote_verification",
                    sequence=int(payload.get("sequence") or 1),
                    provenance={
                        "execution_id": execution_id,
                        "attempt_id": attempt_id,
                        "mission_id": task.task_id,
                        "project_id": task.workspace_id,
                    },
                    payload={"outcome": "verified", "passed": True},
                )
            )

        outcome = await self.completion.complete_from_remote_event(
            context=CompletionContext(
                organisation_id=None,
                project_id=task.workspace_id,
                mission_id=task.task_id,
                execution_id=execution_id,
                attempt_id=attempt_id,
                authoritative_attempt_id=authoritative,
                backend_id=task.selected_backend_id or "joymesh",
                harness_id=task.selected_harness_id or task.selected_connector_id,
                correlation_id=task.task_id,
                user_id=task.user_id,
                cancelled=event_type in {"task.cancelled", "execution.cancelled"},
                require_evidence=True,
                verification_strategy="remote_verifier_event",
                required_evidence_types=("remote_verification",),
            ),
            event_type=event_type,
            payload=payload,
            candidate_evidence=candidates,
        )
        for event in outcome.events:
            await self.store.audit(
                str(event.get("event_type") or "completion.event"),
                task_id=task_id,
                payload=dict(event),
            )
            self.store.append_event(task_id, dict(event))

        # Stale/superseded/late attempts must not terminalise the mission.
        if outcome.failure_class in {
            CompletionFailureClass.STALE_ATTEMPT.value,
            CompletionFailureClass.SUPERSEDED_ATTEMPT.value,
            CompletionFailureClass.LATE_EVENT.value,
        }:
            self.metrics.stale_event_rejections += 1
            return task

        if task.status in {
            RuntimeTaskStatus.SUCCEEDED,
            RuntimeTaskStatus.FAILED,
            RuntimeTaskStatus.CANCELLED,
            RuntimeTaskStatus.REJECTED,
        }:
            # Already terminal — idempotent remote delivery.
            return task

        if outcome.state is CompletionLifecycleState.COMPLETED:
            new_status = RuntimeTaskStatus.SUCCEEDED
        elif outcome.state is CompletionLifecycleState.CANCELLED:
            new_status = RuntimeTaskStatus.CANCELLED
        elif outcome.state in {
            CompletionLifecycleState.BLOCKED,
            CompletionLifecycleState.TIMED_OUT,
            CompletionLifecycleState.FAILED,
        }:
            new_status = RuntimeTaskStatus.FAILED
        elif event_type in {"task.failed", "execution.failed"}:
            new_status = RuntimeTaskStatus.FAILED
        else:
            new_status = RuntimeTaskStatus.FAILED

        updated = task.model_copy(
            update={
                "status": new_status,
                "detail": outcome.detail,
                "updated_at": utc_now(),
            }
        )
        await self.store.save_task(updated)
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
                payload={
                    "status": new_status.value,
                    "event_type": event_type,
                    "completion_state": outcome.state.value,
                    "verified": outcome.ok,
                },
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
        execution_id = task.execution_id or task_id
        if self.execution_routing is not None:
            cancel_result = await self.execution_routing.router.cancel(execution_id)
            await self.store.audit(
                "backend.cancelled",
                task_id=task_id,
                payload=dict(cancel_result),
            )
            self.store.append_event(
                task_id,
                {
                    "event_type": "backend.cancelled",
                    "execution_id": execution_id,
                    **dict(cancel_result),
                },
            )
        lease = self.leases.active_lease(task_id)
        if lease is not None and lease.status.value == "active":
            self.leases.release(task_id, lease.fencing_token)
        if self.completion is not None:
            execution_id = task.execution_id or task_id
            attempt_id = self._authoritative_attempts.get(execution_id, execution_id)
            outcome = await self.completion.finalise_cancelled(
                CompletionContext(
                    organisation_id=None,
                    project_id=task.workspace_id,
                    mission_id=task.task_id,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    authoritative_attempt_id=attempt_id,
                    backend_id=task.selected_backend_id or "unknown",
                    harness_id=task.selected_harness_id or task.selected_connector_id,
                    cancelled=True,
                    user_id=task.user_id,
                ),
                cleanup_completed=True,
            )
            for event in outcome.events:
                await self.store.audit(
                    str(event.get("event_type") or "completion.event"),
                    task_id=task_id,
                    payload=dict(event),
                )
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

    async def _attach_provider_route(
        self,
        task: RuntimeTaskRecord,
        request: RuntimeTaskRequest,
    ) -> RuntimeTaskRecord:
        """Deprecated: provider routing is owned by FireConnectBackend via ExecutionRouter.

        Kept for compatibility with older call sites/tests that annotate route metadata.
        Does not call ProviderRouteService and does not mutate FireConnect.
        """

        del request
        return task


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
        preferred_providers=task.preferred_providers,
        required_provider=task.required_provider,
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
    return build_ready_connector_node(
        node_id=node_id,
        workspace_id=workspace_id,
        connector_id="cursor",
        local_path=local_path,
        online=online,
        revoked=revoked,
    )


def build_ready_codex_node(
    *,
    node_id: str,
    workspace_id: str,
    local_path: str = "/tmp/workspace",
    online: bool = True,
    revoked: bool = False,
) -> SchedulerNodeSnapshot:
    return build_ready_connector_node(
        node_id=node_id,
        workspace_id=workspace_id,
        connector_id="codex",
        local_path=local_path,
        online=online,
        revoked=revoked,
    )


def build_ready_opencode_node(
    *,
    node_id: str,
    workspace_id: str,
    local_path: str = "/tmp/workspace",
    online: bool = True,
    revoked: bool = False,
) -> SchedulerNodeSnapshot:
    return build_ready_connector_node(
        node_id=node_id,
        workspace_id=workspace_id,
        connector_id="opencode",
        local_path=local_path,
        online=online,
        revoked=revoked,
    )


def build_ready_claude_node(
    *,
    node_id: str,
    workspace_id: str,
    local_path: str = "/tmp/workspace",
    online: bool = True,
    revoked: bool = False,
) -> SchedulerNodeSnapshot:
    return build_ready_connector_node(
        node_id=node_id,
        workspace_id=workspace_id,
        connector_id="claude",
        local_path=local_path,
        online=online,
        revoked=revoked,
    )


def build_ready_grok_node(
    *,
    node_id: str,
    workspace_id: str,
    local_path: str = "/tmp/workspace",
    online: bool = True,
    revoked: bool = False,
) -> SchedulerNodeSnapshot:
    return build_ready_connector_node(
        node_id=node_id,
        workspace_id=workspace_id,
        connector_id="grok",
        local_path=local_path,
        online=online,
        revoked=revoked,
    )


def build_ready_connector_node(
    *,
    node_id: str,
    workspace_id: str,
    connector_id: str,
    local_path: str = "/tmp/workspace",
    online: bool = True,
    revoked: bool = False,
    extra_connectors: Mapping[str, SchedulerConnectorSnapshot] | None = None,
) -> SchedulerNodeSnapshot:
    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        EvidenceTrustLevel,
        NodeConnectorState,
    )
    from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES

    connectors: dict[str, SchedulerConnectorSnapshot] = {
        connector_id: SchedulerConnectorSnapshot(
            connector_id=connector_id,
            installed=True,
            readiness=NodeConnectorState.READY,
            authenticated=True,
            routing_enabled=True,
            certified_capabilities=READ_ONLY_CAPABILITIES,
            trust_level=EvidenceTrustLevel.NODE_ATTESTED,
            execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
        )
    }
    if extra_connectors:
        connectors.update(extra_connectors)
    return SchedulerNodeSnapshot(
        node_id=node_id,
        online=online,
        revoked=revoked,
        session_authenticated=online and not revoked,
        connectors=connectors,
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
