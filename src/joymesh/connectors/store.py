"""SQL-backed connector lifecycle persistence."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select

from joymesh.connectors.lifecycle_models import (
    ACTIVE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    ConnectorEvidence,
    ConnectorEvidenceType,
    ConnectorReadiness,
    ConnectorTaskEvent,
    ConnectorTaskRecord,
    ConnectorTaskStatus,
)
from joymesh.connectors.planning import ConnectorTaskPlan
from joymesh.connectors.readiness import ConnectorReadinessService, _NodeConnectorSnapshot
from joymesh.models import utc_now
from joymesh.persistence import (
    ConnectorEvidenceRow,
    ConnectorTaskEventRow,
    ConnectorTaskPlanRow,
    ConnectorTaskRow,
    Database,
    NodeConnectorAuthenticationRow,
    NodeConnectorCertificationRow,
    NodeConnectorDiscoveryRow,
    NodeConnectorInstallationRow,
    NodeConnectorReadinessRow,
)


class ConnectorLifecycleStore:
    def __init__(self, database: Database, *, platform: str = "darwin") -> None:
        self.database = database
        self.platform = platform
        self.readiness = ConnectorReadinessService()

    async def save_plan(self, plan: ConnectorTaskPlan) -> None:
        row = ConnectorTaskPlanRow(
            id=plan.plan_id,
            node_id=plan.node_id,
            connector_id=plan.connector_id,
            connector_revision=plan.connector_revision,
            action=plan.action.value,
            plan_hash=plan.plan_hash,
            plan_json=plan.model_dump_json(),
            expires_at=plan.expires_at,
        )
        async with self.database.sessions() as session:
            session.add(row)
            await session.commit()

    async def get_plan(self, plan_id: str) -> ConnectorTaskPlan:
        async with self.database.sessions() as session:
            row = await session.get(ConnectorTaskPlanRow, plan_id)
            if row is None:
                raise KeyError(f"unknown or expired connector plan: {plan_id}")
        return ConnectorTaskPlan.model_validate_json(row.plan_json)

    async def create_task_from_plan(
        self,
        plan: ConnectorTaskPlan,
        *,
        previous_task_id: str | None = None,
    ) -> ConnectorTaskRecord:
        task_id = str(uuid4())
        idempotency = f"{plan.node_id}:{task_id}:{plan.plan_hash}"
        record = ConnectorTaskRecord(
            task_id=task_id,
            plan_id=plan.plan_id,
            node_id=plan.node_id,
            connector_id=plan.connector_id,
            connector_revision=plan.connector_revision,
            action=plan.action.value,
            plan_hash=plan.plan_hash,
            status=ConnectorTaskStatus.QUEUED,
            idempotency_key=idempotency,
            previous_task_id=previous_task_id,
            created_at=utc_now(),
        )
        row = ConnectorTaskRow(
            id=record.task_id,
            plan_id=record.plan_id,
            node_id=record.node_id,
            connector_id=record.connector_id,
            connector_revision=record.connector_revision,
            action=record.action,
            plan_hash=record.plan_hash,
            status=record.status.value,
            idempotency_key=record.idempotency_key,
            previous_task_id=record.previous_task_id,
            version=record.version,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            detail=record.detail,
        )
        async with self.database.sessions() as session:
            session.add(row)
            await session.commit()
        return record

    async def get_task(self, task_id: str) -> ConnectorTaskRecord:
        async with self.database.sessions() as session:
            row = await session.get(ConnectorTaskRow, task_id)
            if row is None:
                raise KeyError(f"unknown connector task: {task_id}")
        return _task_record(row)

    async def list_active_tasks(self, *, node_id: str) -> tuple[ConnectorTaskRecord, ...]:
        active_values = tuple(status.value for status in ACTIVE_TASK_STATUSES)
        async with self.database.sessions() as session:
            rows = (
                await session.scalars(
                    select(ConnectorTaskRow)
                    .where(
                        ConnectorTaskRow.node_id == node_id,
                        ConnectorTaskRow.status.in_(active_values),
                    )
                    .order_by(ConnectorTaskRow.started_at.desc().nulls_last())
                )
            ).all()
        return tuple(_task_record(row) for row in rows)

    async def transition_task(
        self,
        task_id: str,
        *,
        expected_version: int,
        status: ConnectorTaskStatus,
        detail: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> ConnectorTaskRecord:
        async with self.database.sessions() as session:
            row = await session.get(ConnectorTaskRow, task_id)
            if row is None:
                raise KeyError(f"unknown connector task: {task_id}")
            if row.version != expected_version:
                raise ValueError("connector task version conflict")
            if row.status in {item.value for item in TERMINAL_TASK_STATUSES}:
                raise ValueError("connector task already terminal")
            row.status = status.value
            row.version = expected_version + 1
            if detail is not None:
                row.detail = detail
            if started_at is not None:
                row.started_at = started_at
            if finished_at is not None:
                row.finished_at = finished_at
            await session.commit()
        return await self.get_task(task_id)

    async def append_task_event(self, event: ConnectorTaskEvent) -> ConnectorTaskEvent:
        task = await self.get_task(event.task_id)
        if task.node_id != event.node_id or task.connector_id != event.connector_id:
            raise ValueError("connector task event identity mismatch")
        if task.status in TERMINAL_TASK_STATUSES:
            raise ValueError("cannot append events to terminal connector task")
        row = ConnectorTaskEventRow(
            id=event.event_id,
            task_id=event.task_id,
            node_id=event.node_id,
            connector_id=event.connector_id,
            event_type=event.event_type,
            sequence=event.sequence,
            payload_json=json.dumps(event.payload, sort_keys=True),
            created_at=event.created_at,
        )
        async with self.database.sessions() as session:
            session.add(row)
            await session.commit()
        return event

    async def list_task_events(
        self, task_id: str, *, after: int = 0
    ) -> tuple[ConnectorTaskEvent, ...]:
        async with self.database.sessions() as session:
            rows = (
                await session.scalars(
                    select(ConnectorTaskEventRow)
                    .where(
                        ConnectorTaskEventRow.task_id == task_id,
                        ConnectorTaskEventRow.sequence > after,
                    )
                    .order_by(ConnectorTaskEventRow.sequence)
                )
            ).all()
        return tuple(
            ConnectorTaskEvent(
                event_id=row.id,
                task_id=row.task_id,
                node_id=row.node_id,
                connector_id=row.connector_id,
                event_type=row.event_type,
                sequence=row.sequence,
                payload=json.loads(row.payload_json),
                created_at=row.created_at,
            )
            for row in rows
        )

    async def record_evidence(self, evidence: ConnectorEvidence) -> ConnectorEvidence:
        row = ConnectorEvidenceRow(
            id=str(evidence.evidence_id),
            node_id=str(evidence.node_id),
            connector_id=evidence.connector_id,
            connector_revision=evidence.connector_revision,
            task_id=str(evidence.task_id),
            evidence_type=evidence.evidence_type.value,
            status=evidence.status,
            executable_path=evidence.executable_path,
            executable_fingerprint=evidence.executable_fingerprint,
            harness_version=evidence.harness_version,
            provider_mode=evidence.provider_mode,
            details_json=json.dumps(dict(evidence.details), sort_keys=True),
            trust_level=evidence.trust_level.value,
            execution_origin=evidence.execution_origin.value,
            created_at=evidence.created_at,
            expires_at=evidence.expires_at,
        )
        async with self.database.sessions() as session:
            session.add(row)
            await session.commit()
        await self._apply_evidence_side_effects(evidence)
        await self.recompute(node_id=str(evidence.node_id), connector_id=evidence.connector_id)
        return evidence

    async def recompute(self, *, node_id: str, connector_id: str) -> ConnectorReadiness:
        snapshot = await self._load_snapshot(node_id=node_id, connector_id=connector_id)
        readiness = self.readiness.derive_from_snapshot(
            node_id=node_id, connector_id=connector_id, snapshot=snapshot
        )
        row = NodeConnectorReadinessRow(
            id=str(uuid4()),
            node_id=node_id,
            connector_id=connector_id,
            state=readiness.state.value,
            recommended_action=(
                readiness.recommended_action.value if readiness.recommended_action else None
            ),
            blocking_reason=readiness.blocking_reason,
            active_task_id=readiness.active_task_id,
            latest_evidence_id=readiness.latest_evidence_id,
            routing_eligible=readiness.routing_eligible,
            snapshot_json=json.dumps(
                {
                    "catalogue_maturity": readiness.catalogue_maturity,
                    "installed_version": readiness.installed_version,
                    "executable_path": readiness.executable_path,
                    "routing_profile": readiness.routing_profile,
                    "evidence_trust_level": (
                        readiness.evidence_trust_level.value
                        if readiness.evidence_trust_level
                        else None
                    ),
                    "execution_origin": (
                        readiness.execution_origin.value if readiness.execution_origin else None
                    ),
                },
                sort_keys=True,
            ),
            recomputed_at=readiness.updated_at,
        )
        async with self.database.sessions() as session:
            await session.execute(
                delete(NodeConnectorReadinessRow).where(
                    NodeConnectorReadinessRow.node_id == node_id,
                    NodeConnectorReadinessRow.connector_id == connector_id,
                )
            )
            session.add(row)
            await session.commit()
        return readiness

    async def get_readiness(self, *, node_id: str, connector_id: str) -> ConnectorReadiness:
        async with self.database.sessions() as session:
            row = await session.scalar(
                select(NodeConnectorReadinessRow)
                .where(
                    NodeConnectorReadinessRow.node_id == node_id,
                    NodeConnectorReadinessRow.connector_id == connector_id,
                )
                .order_by(NodeConnectorReadinessRow.recomputed_at.desc())
            )
        if row is None:
            return await self.recompute(node_id=node_id, connector_id=connector_id)
        snapshot = await self._load_snapshot(node_id=node_id, connector_id=connector_id)
        return self.readiness.derive_from_snapshot(
            node_id=node_id, connector_id=connector_id, snapshot=snapshot
        )

    async def list_readiness(self, *, node_id: str) -> tuple[ConnectorReadiness, ...]:
        from joymesh.connectors import ConnectorCatalogue

        catalogue = ConnectorCatalogue.builtins()
        results: list[ConnectorReadiness] = []
        for item in catalogue.all():
            results.append(await self.get_readiness(node_id=node_id, connector_id=item.harness_id))
        return tuple(results)

    async def _load_snapshot(self, *, node_id: str, connector_id: str) -> _NodeConnectorSnapshot:
        async with self.database.sessions() as session:
            discovery = await session.scalar(
                select(NodeConnectorDiscoveryRow)
                .where(
                    NodeConnectorDiscoveryRow.node_id == node_id,
                    NodeConnectorDiscoveryRow.connector_id == connector_id,
                )
                .order_by(NodeConnectorDiscoveryRow.discovered_at.desc())
            )
            installation = await session.scalar(
                select(NodeConnectorInstallationRow)
                .where(
                    NodeConnectorInstallationRow.node_id == node_id,
                    NodeConnectorInstallationRow.connector_id == connector_id,
                )
                .order_by(NodeConnectorInstallationRow.installed_at.desc())
            )
            auth = await session.scalar(
                select(NodeConnectorAuthenticationRow)
                .where(
                    NodeConnectorAuthenticationRow.node_id == node_id,
                    NodeConnectorAuthenticationRow.connector_id == connector_id,
                )
                .order_by(NodeConnectorAuthenticationRow.verified_at.desc().nulls_last())
            )
            certification = await session.scalar(
                select(NodeConnectorCertificationRow)
                .where(
                    NodeConnectorCertificationRow.node_id == node_id,
                    NodeConnectorCertificationRow.connector_id == connector_id,
                )
                .order_by(NodeConnectorCertificationRow.certified_at.desc())
            )
            evidence_rows = (
                await session.scalars(
                    select(ConnectorEvidenceRow)
                    .where(
                        ConnectorEvidenceRow.node_id == node_id,
                        ConnectorEvidenceRow.connector_id == connector_id,
                    )
                    .order_by(ConnectorEvidenceRow.created_at.desc())
                )
            ).all()
            active = await session.scalar(
                select(ConnectorTaskRow)
                .where(
                    ConnectorTaskRow.node_id == node_id,
                    ConnectorTaskRow.connector_id == connector_id,
                    ConnectorTaskRow.status.in_(
                        tuple(status.value for status in ACTIVE_TASK_STATUSES)
                    ),
                )
                .order_by(ConnectorTaskRow.started_at.desc().nulls_last())
            )

        evidence_by_type: dict[ConnectorEvidenceType, dict[str, object]] = {}
        latest_evidence_id: str | None = None
        executable_fingerprint: str | None = None
        for row in evidence_rows:
            if latest_evidence_id is None:
                latest_evidence_id = row.id
            kind = ConnectorEvidenceType(row.evidence_type)
            if kind not in evidence_by_type:
                details = json.loads(row.details_json)
                evidence_by_type[kind] = {
                    **details,
                    "status": row.status,
                    "connector_revision": row.connector_revision,
                    "harness_version": row.harness_version,
                    "detail": details.get("detail"),
                    "trust_level": row.trust_level,
                    "execution_origin": row.execution_origin,
                    "evidence_id": row.id,
                    "executable_fingerprint": row.executable_fingerprint,
                }
            if row.executable_fingerprint and executable_fingerprint is None:
                executable_fingerprint = row.executable_fingerprint

        cert_valid = False
        cert_expires: datetime | None = None
        if certification is not None:
            cert_valid = True
            cert_expires = certification.expires_at

        active_status: ConnectorTaskStatus | None = None
        active_id: str | None = None
        active_action: str | None = None
        if active is not None:
            active_status = ConnectorTaskStatus(active.status)
            active_id = active.id
            active_action = active.action

        return _NodeConnectorSnapshot(
            discovery_executable=discovery.executable if discovery else None,
            discovery_version=discovery.version if discovery else None,
            discovery_revision=discovery.connector_revision if discovery else None,
            discovery_environment=discovery.execution_environment if discovery else None,
            installation_executable=installation.executable if installation else None,
            installation_version=installation.version if installation else None,
            installation_revision=installation.connector_revision if installation else None,
            installation_routing_enabled=bool(installation.enabled_for_routing)
            if installation
            else False,
            auth_status=auth.status if auth else None,
            auth_verified_at=auth.verified_at if auth else None,
            auth_method_id=auth.method_id if auth else None,
            certification_valid=cert_valid,
            certification_expires_at=cert_expires,
            evidence_by_type=evidence_by_type,
            active_task_status=active_status,
            active_task_id=active_id,
            active_task_action=active_action,
            latest_evidence_id=latest_evidence_id,
            executable_fingerprint=executable_fingerprint,
            platform=self.platform,
            node_online=True,
        )

    async def _apply_evidence_side_effects(self, evidence: ConnectorEvidence) -> None:
        node_id = str(evidence.node_id)
        now = evidence.created_at
        async with self.database.sessions() as session:
            if evidence.evidence_type is ConnectorEvidenceType.DISCOVERY:
                session.add(
                    NodeConnectorDiscoveryRow(
                        id=str(uuid4()),
                        node_id=node_id,
                        connector_id=evidence.connector_id,
                        connector_revision=evidence.connector_revision,
                        executable=evidence.executable_path,
                        version=evidence.harness_version,
                        execution_environment=str(
                            evidence.details.get("execution_environment", "host")
                        ),
                        discovered_at=now,
                    )
                )
            elif evidence.evidence_type is ConnectorEvidenceType.INSTALLATION:
                session.add(
                    NodeConnectorInstallationRow(
                        id=str(uuid4()),
                        node_id=node_id,
                        connector_id=evidence.connector_id,
                        connector_revision=evidence.connector_revision,
                        method_id=str(evidence.details.get("method_id", "unknown")),
                        executable=evidence.executable_path or "",
                        version=evidence.harness_version,
                        enabled_for_routing=False,
                        installed_at=now,
                    )
                )
            elif evidence.evidence_type is ConnectorEvidenceType.AUTHENTICATION:
                session.add(
                    NodeConnectorAuthenticationRow(
                        id=str(uuid4()),
                        node_id=node_id,
                        connector_id=evidence.connector_id,
                        method_id=str(evidence.details.get("method_id", "default")),
                        status=evidence.status,
                        verified_at=now if evidence.status == "authenticated" else None,
                        detail=str(evidence.details.get("detail", "")),
                    )
                )
            elif evidence.evidence_type is ConnectorEvidenceType.CERTIFICATION:
                session.add(
                    NodeConnectorCertificationRow(
                        id=str(uuid4()),
                        node_id=node_id,
                        connector_id=evidence.connector_id,
                        connector_revision=evidence.connector_revision,
                        harness_version=evidence.harness_version or "unknown",
                        executable_fingerprint=evidence.executable_fingerprint or "",
                        evidence_digest=str(evidence.details.get("evidence_digest", "")),
                        passed_levels_json=json.dumps(
                            evidence.details.get("passed_levels", {}), sort_keys=True
                        ),
                        certified_at=now,
                        expires_at=evidence.expires_at,
                    )
                )
            await session.commit()

    async def set_routing_enabled(self, *, node_id: str, connector_id: str, enabled: bool) -> None:
        async with self.database.sessions() as session:
            installation = await session.scalar(
                select(NodeConnectorInstallationRow)
                .where(
                    NodeConnectorInstallationRow.node_id == node_id,
                    NodeConnectorInstallationRow.connector_id == connector_id,
                )
                .order_by(NodeConnectorInstallationRow.installed_at.desc())
            )
            if installation is None:
                session.add(
                    NodeConnectorInstallationRow(
                        id=str(uuid4()),
                        node_id=node_id,
                        connector_id=connector_id,
                        connector_revision=self.readiness.catalogue.get(connector_id).revision,
                        method_id="routing",
                        executable="",
                        version=None,
                        enabled_for_routing=enabled,
                        installed_at=utc_now(),
                    )
                )
            else:
                installation.enabled_for_routing = enabled
            await session.commit()


def _task_record(row: ConnectorTaskRow) -> ConnectorTaskRecord:
    return ConnectorTaskRecord(
        task_id=row.id,
        plan_id=row.plan_id,
        node_id=row.node_id,
        connector_id=row.connector_id,
        connector_revision=row.connector_revision,
        action=row.action,
        plan_hash=row.plan_hash,
        status=ConnectorTaskStatus(row.status),
        idempotency_key=row.idempotency_key,
        previous_task_id=row.previous_task_id,
        version=row.version,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        detail=row.detail,
    )
