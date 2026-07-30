"""Connector lifecycle orchestration: plans, tasks, node execution, and readiness."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from joymesh.connectors.lifecycle_models import (
    TERMINAL_TASK_STATUSES,
    ConnectorEvidence,
    ConnectorExecutionOrigin,
    ConnectorLifecyclePlanResponse,
    ConnectorReadiness,
    ConnectorTaskEvent,
    ConnectorTaskRecord,
    ConnectorTaskStatus,
    EvidenceTrustLevel,
    NodeConnectorState,
)
from joymesh.connectors.node_runner import ConnectorNodeRunner
from joymesh.connectors.planning import ConnectorAction, ConnectorPlanner, ConnectorTaskPlan
from joymesh.connectors.store import ConnectorLifecycleStore
from joymesh.control_plane.security import inline_connector_node_enabled, production_mode
from joymesh.models import utc_now
from joymesh.persistence import Database

TaskOfferCallback = Callable[[ConnectorTaskRecord, ConnectorTaskPlan], Awaitable[bool | None]]


@dataclass
class ConnectorLifecycleCoordinator:
    database: Database
    planner: ConnectorPlanner
    store: ConnectorLifecycleStore
    _runners: dict[str, ConnectorNodeRunner] = field(default_factory=dict)
    _inline_node: bool = field(default_factory=inline_connector_node_enabled)
    _offer_callbacks: list[TaskOfferCallback] = field(default_factory=list)
    _background: list[asyncio.Task[None]] = field(default_factory=list)

    def register_offer_callback(self, callback: TaskOfferCallback) -> None:
        self._offer_callbacks.append(callback)

    async def persist_plan(self, plan: ConnectorTaskPlan) -> ConnectorTaskPlan:
        self.planner.store.put(plan)
        await self.store.save_plan(plan)
        return plan

    async def approve_and_queue(
        self,
        plan_id: str,
        *,
        plan_hash: str,
        approved: bool,
    ) -> ConnectorTaskRecord:
        try:
            plan = await self.store.get_plan(plan_id)
        except KeyError:
            plan = self.planner.store.get(plan_id)
            await self.store.save_plan(plan)
        self.planner.store.put(plan)
        self.planner.validate(plan)
        if not approved or plan.plan_hash != plan_hash:
            raise PermissionError("exact connector plan approval required")
        if plan.action is ConnectorAction.AUTHENTICATE:
            existing = await self._find_active_login(plan)
            if existing is not None:
                return existing
        task = await self.store.create_task_from_plan(plan)
        offered = False
        for callback in self._offer_callbacks:
            result = await callback(task, plan)
            offered = offered or bool(result)
        if offered:
            task = await self.store.transition_task(
                task.task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.OFFERED_TO_NODE,
            )
        elif self._inline_node:
            task = await self.store.transition_task(
                task.task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.OFFERED_TO_NODE,
            )
            handle = asyncio.create_task(
                self._run_on_node(task.task_id, plan),
                name=f"connector-task-{task.task_id}",
            )
            self._background.append(handle)
            handle.add_done_callback(
                lambda finished: (
                    self._background.remove(finished) if finished in self._background else None
                )
            )
        else:
            task = await self.store.get_task(task.task_id)
        return task

    async def _find_active_login(self, plan: ConnectorTaskPlan) -> ConnectorTaskRecord | None:
        for task in await self.store.list_active_tasks(node_id=plan.node_id):
            if task.connector_id != plan.connector_id:
                continue
            if task.action != ConnectorAction.AUTHENTICATE.value:
                continue
            if task.status in {
                ConnectorTaskStatus.WAITING_FOR_USER,
                ConnectorTaskStatus.WAITING_FOR_AUTH_CALLBACK,
                ConnectorTaskStatus.QUEUED,
                ConnectorTaskStatus.OFFERED_TO_NODE,
                ConnectorTaskStatus.ACCEPTED_BY_NODE,
                ConnectorTaskStatus.RUNNING,
            }:
                return task
        return None

    async def offer_queued_tasks(self, *, node_id: str) -> tuple[ConnectorTaskRecord, ...]:
        offered: list[ConnectorTaskRecord] = []
        for task in await self.store.list_active_tasks(node_id=node_id):
            if task.status is not ConnectorTaskStatus.QUEUED:
                continue
            plan = await self.store.get_plan(task.plan_id)
            success = False
            for callback in self._offer_callbacks:
                success = success or bool(await callback(task, plan))
            if success:
                offered.append(
                    await self.store.transition_task(
                        task.task_id,
                        expected_version=task.version,
                        status=ConnectorTaskStatus.OFFERED_TO_NODE,
                    )
                )
        return tuple(offered)

    async def _run_on_node(self, task_id: str, plan: ConnectorTaskPlan) -> None:
        runner = self._runners.setdefault(
            plan.node_id,
            ConnectorNodeRunner(
                node_id=plan.node_id,
                execution_origin=ConnectorExecutionOrigin.INLINE_DEVELOPMENT,
            ),
        )
        task = await self.store.get_task(task_id)
        task = await self.store.transition_task(
            task_id,
            expected_version=task.version,
            status=ConnectorTaskStatus.ACCEPTED_BY_NODE,
            started_at=utc_now(),
        )
        await self.store.transition_task(
            task_id,
            expected_version=task.version,
            status=ConnectorTaskStatus.RUNNING,
        )

        async def emit_event(event: ConnectorTaskEvent) -> None:
            current = await self.store.get_task(task_id)
            if current.status in TERMINAL_TASK_STATUSES:
                return
            if event.event_type in {
                "task.succeeded",
                "task.failed",
                "task.cancelled",
                "task.expired",
                "task.interrupted",
            }:
                return
            await self.store.append_task_event(event)

        async def record_evidence(evidence: ConnectorEvidence) -> None:
            if evidence.execution_origin is ConnectorExecutionOrigin.REMOTE_NODE:
                evidence = ConnectorEvidence(
                    evidence_id=evidence.evidence_id,
                    node_id=evidence.node_id,
                    connector_id=evidence.connector_id,
                    connector_revision=evidence.connector_revision,
                    task_id=evidence.task_id,
                    evidence_type=evidence.evidence_type,
                    status=evidence.status,
                    executable_path=evidence.executable_path,
                    executable_fingerprint=evidence.executable_fingerprint,
                    harness_version=evidence.harness_version,
                    provider_mode=evidence.provider_mode,
                    details={
                        **dict(evidence.details),
                        "execution_origin": ConnectorExecutionOrigin.INLINE_DEVELOPMENT.value,
                        "trust_level": EvidenceTrustLevel.DEVELOPMENT.value,
                    },
                    created_at=evidence.created_at,
                    expires_at=evidence.expires_at,
                    trust_level=EvidenceTrustLevel.DEVELOPMENT,
                    execution_origin=ConnectorExecutionOrigin.INLINE_DEVELOPMENT,
                )
            await self.store.record_evidence(evidence)

        final = await runner.execute(
            task_id=task_id,
            plan=plan,
            emit_event=emit_event,
            record_evidence=record_evidence,
        )
        if final not in TERMINAL_TASK_STATUSES:
            current = await self.store.get_task(task_id)
            if current.status not in TERMINAL_TASK_STATUSES:
                await self.store.transition_task(
                    task_id,
                    expected_version=current.version,
                    status=final,
                )
                await self.store.recompute(node_id=plan.node_id, connector_id=plan.connector_id)
            return
        await self._finalize_task(task_id, final)

    async def _finalize_task(
        self,
        task_id: str,
        status: ConnectorTaskStatus,
        *,
        detail: str | None = None,
    ) -> None:
        task = await self.store.get_task(task_id)
        if task.status in TERMINAL_TASK_STATUSES:
            return
        await self.store.transition_task(
            task_id,
            expected_version=task.version,
            status=status,
            detail=detail,
            finished_at=utc_now(),
        )
        await self.store.recompute(node_id=task.node_id, connector_id=task.connector_id)

    async def cancel_task(self, task_id: str) -> ConnectorTaskRecord:
        task = await self.store.get_task(task_id)
        if task.status in TERMINAL_TASK_STATUSES:
            return task
        updated = await self.store.transition_task(
            task_id,
            expected_version=task.version,
            status=ConnectorTaskStatus.CANCELLED,
            finished_at=utc_now(),
        )
        await self.store.recompute(node_id=task.node_id, connector_id=task.connector_id)
        return updated

    async def retry_task(self, task_id: str) -> ConnectorTaskRecord:
        previous = await self.store.get_task(task_id)
        if previous.status not in {
            ConnectorTaskStatus.FAILED,
            ConnectorTaskStatus.CANCELLED,
            ConnectorTaskStatus.INTERRUPTED,
            ConnectorTaskStatus.EXPIRED,
        }:
            raise ValueError("only failed or cancelled tasks can retry")
        plan = await self.store.get_plan(previous.plan_id)
        task = await self.store.create_task_from_plan(plan, previous_task_id=previous.task_id)
        offered = False
        for callback in self._offer_callbacks:
            offered = offered or bool(await callback(task, plan))
        if offered:
            return await self.store.transition_task(
                task.task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.OFFERED_TO_NODE,
            )
        if self._inline_node:
            task = await self.store.transition_task(
                task.task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.OFFERED_TO_NODE,
            )
            handle = asyncio.create_task(
                self._run_on_node(task.task_id, plan),
                name=f"connector-task-{task.task_id}",
            )
            self._background.append(handle)
            return task
        return task

    async def authentication_complete(self, task_id: str) -> ConnectorTaskRecord:
        task = await self.store.get_task(task_id)
        if task.status not in {
            ConnectorTaskStatus.WAITING_FOR_USER,
            ConnectorTaskStatus.WAITING_FOR_AUTH_CALLBACK,
            ConnectorTaskStatus.RUNNING,
            ConnectorTaskStatus.SUCCEEDED,
        }:
            raise ValueError("task is not waiting for authentication")
        verify_plan = self.planner.plan(
            node_id=task.node_id,
            connector_id=task.connector_id,
            action=ConnectorAction.VERIFY_AUTHENTICATION,
        )
        await self.persist_plan(verify_plan)
        if task.status not in TERMINAL_TASK_STATUSES:
            await self.store.transition_task(
                task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.SUCCEEDED,
                finished_at=utc_now(),
            )
        return await self.approve_and_queue(
            verify_plan.plan_id, plan_hash=verify_plan.plan_hash, approved=True
        )

    async def enable_routing(self, *, node_id: str, connector_id: str) -> ConnectorReadiness:
        readiness = await self.store.get_readiness(node_id=node_id, connector_id=connector_id)
        if readiness.state is not NodeConnectorState.ROUTING_DISABLED:
            raise PermissionError(
                f"routing can only be enabled from routing_disabled (got {readiness.state.value})"
            )
        if production_mode():
            if readiness.evidence_trust_level is not EvidenceTrustLevel.NODE_ATTESTED:
                raise PermissionError(
                    "production routing requires node-attested certification evidence"
                )
            if readiness.execution_origin is not ConnectorExecutionOrigin.REMOTE_NODE:
                raise PermissionError("production routing requires remote_node execution origin")
        await self.store.set_routing_enabled(
            node_id=node_id, connector_id=connector_id, enabled=True
        )
        return await self.store.recompute(node_id=node_id, connector_id=connector_id)

    async def disable_routing(self, *, node_id: str, connector_id: str) -> ConnectorReadiness:
        await self.store.set_routing_enabled(
            node_id=node_id, connector_id=connector_id, enabled=False
        )
        return await self.store.recompute(node_id=node_id, connector_id=connector_id)

    async def get_task(self, task_id: str) -> ConnectorTaskRecord:
        return await self.store.get_task(task_id)

    async def list_task_events(
        self, task_id: str, *, after: int = 0
    ) -> tuple[ConnectorTaskEvent, ...]:
        return await self.store.list_task_events(task_id, after=after)

    async def list_active_tasks(self, *, node_id: str) -> tuple[ConnectorTaskRecord, ...]:
        return await self.store.list_active_tasks(node_id=node_id)

    async def get_readiness(self, *, node_id: str, connector_id: str) -> ConnectorReadiness:
        return await self.store.get_readiness(node_id=node_id, connector_id=connector_id)

    async def list_readiness(self, *, node_id: str) -> tuple[ConnectorReadiness, ...]:
        return await self.store.list_readiness(node_id=node_id)

    def plan_response(
        self, plan: ConnectorTaskPlan, *, task_id: str | None = None
    ) -> ConnectorLifecyclePlanResponse:
        return ConnectorLifecyclePlanResponse(
            plan=plan.model_dump(mode="json"),
            task_id=task_id,
            approval_required=True,
            next_action="approve",
        )


def build_coordinator(
    database: Database,
    planner: ConnectorPlanner,
    *,
    platform: str | None = None,
) -> ConnectorLifecycleCoordinator:
    import sys

    store = ConnectorLifecycleStore(database, platform=platform or sys.platform)
    return ConnectorLifecycleCoordinator(database=database, planner=planner, store=store)
