"""Focused tests for connector readiness derivation and lifecycle execution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from joymesh.api import create_app
from joymesh.connectors.lifecycle_models import (
    ConnectorEvidence,
    ConnectorEvidenceType,
    ConnectorTaskStatus,
    NodeConnectorState,
    RecommendedConnectorAction,
)
from joymesh.connectors.planning import ConnectorAction
from joymesh.connectors.readiness import ConnectorReadinessService, _NodeConnectorSnapshot
from joymesh.models import utc_now
from joymesh.persistence import (
    NodeConnectorAuthenticationRow,
    NodeConnectorCertificationRow,
    NodeConnectorDiscoveryRow,
    NodeConnectorInstallationRow,
)
from joymesh.service import JoyMesh


def _snapshot(**overrides: object) -> _NodeConnectorSnapshot:
    base = _NodeConnectorSnapshot(
        discovery_executable=None,
        discovery_version=None,
        discovery_revision=None,
        discovery_environment=None,
        installation_executable=None,
        installation_version=None,
        installation_revision=None,
        installation_routing_enabled=False,
        auth_status=None,
        auth_verified_at=None,
        auth_method_id=None,
        certification_valid=False,
        certification_expires_at=None,
        evidence_by_type={},
        active_task_status=None,
        active_task_id=None,
        active_task_action=None,
        latest_evidence_id=None,
        executable_fingerprint=None,
        platform="darwin",
        node_online=True,
    )
    return _NodeConnectorSnapshot(**{**base.__dict__, **overrides})


def test_catalogue_maturity_does_not_override_node_state() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="codex",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/codex",
            discovery_version="0.1.0",
            discovery_revision="2026-07-29.1",
            evidence_by_type={
                ConnectorEvidenceType.FAILURE: {
                    "status": "broken_executable",
                    "detail": "Native binary missing",
                }
            },
        ),
    )
    assert readiness.catalogue_maturity == "adapter_conformant"
    assert readiness.state is NodeConnectorState.NEEDS_REPAIR
    assert readiness.recommended_action is RecommendedConnectorAction.REPAIR


def test_missing_executable_gives_available_to_install() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="codex",
        snapshot=_snapshot(),
    )
    assert readiness.state is NodeConnectorState.AVAILABLE_TO_INSTALL


def test_installed_requires_authentication() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="cursor",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/cursor-agent",
            discovery_version="2025.09.18",
            discovery_revision="2026-07-29.1",
        ),
    )
    assert readiness.state is NodeConnectorState.AUTHENTICATION_REQUIRED


def test_authenticated_requires_verification() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="cursor",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/cursor-agent",
            discovery_revision="2026-07-29.1",
            auth_status="authenticated",
            auth_verified_at=datetime.now(UTC),
        ),
    )
    assert readiness.state is NodeConnectorState.VERIFICATION_REQUIRED


def test_active_verification_task_in_progress() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="cursor",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/cursor-agent",
            discovery_revision="2026-07-29.1",
            auth_status="authenticated",
            auth_verified_at=datetime.now(UTC),
            active_task_status=ConnectorTaskStatus.RUNNING,
            active_task_id="task-1",
            active_task_action="verify_adapter",
        ),
    )
    assert readiness.state is NodeConnectorState.VERIFICATION_IN_PROGRESS


def test_adapter_passed_requires_certification() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="cursor",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/cursor-agent",
            discovery_revision="2026-07-29.1",
            auth_status="authenticated",
            auth_verified_at=datetime.now(UTC),
            evidence_by_type={
                ConnectorEvidenceType.ADAPTER_CONFORMANCE: {"status": "passed"},
            },
        ),
    )
    assert readiness.state is NodeConnectorState.CERTIFICATION_REQUIRED


def test_certified_routing_enabled_is_ready() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="cursor",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/cursor-agent",
            discovery_revision="2026-07-29.1",
            installation_executable="/usr/local/bin/cursor-agent",
            installation_revision="2026-07-29.1",
            installation_routing_enabled=True,
            auth_status="authenticated",
            auth_verified_at=datetime.now(UTC),
            certification_valid=True,
            evidence_by_type={
                ConnectorEvidenceType.ADAPTER_CONFORMANCE: {"status": "passed"},
            },
        ),
    )
    assert readiness.state is NodeConnectorState.READY
    assert readiness.routing_eligible


def test_certified_routing_disabled() -> None:
    readiness = ConnectorReadinessService().derive_from_snapshot(
        node_id="node-1",
        connector_id="cursor",
        snapshot=_snapshot(
            discovery_executable="/usr/local/bin/cursor-agent",
            discovery_revision="2026-07-29.1",
            installation_executable="/usr/local/bin/cursor-agent",
            installation_revision="2026-07-29.1",
            installation_routing_enabled=False,
            auth_status="authenticated",
            auth_verified_at=datetime.now(UTC),
            certification_valid=True,
            evidence_by_type={
                ConnectorEvidenceType.ADAPTER_CONFORMANCE: {"status": "passed"},
            },
        ),
    )
    assert readiness.state is NodeConnectorState.ROUTING_DISABLED


def test_ide_only_and_blocked() -> None:
    service = ConnectorReadinessService()
    assert (
        service.derive_from_snapshot(
            node_id="node-1", connector_id="roo-code", snapshot=_snapshot()
        ).state
        is NodeConnectorState.IDE_ONLY
    )
    assert (
        service.derive_from_snapshot(
            node_id="node-1", connector_id="amazon-q", snapshot=_snapshot()
        ).state
        is NodeConnectorState.BLOCKED
    )


async def test_discovery_evidence_advances_readiness(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    await mesh.initialize()
    store = mesh.connector_lifecycle.store
    await store.record_evidence(
        ConnectorEvidence(
            evidence_id=str(uuid4()),
            node_id="node-1",
            connector_id="cursor",
            connector_revision="2026-07-29.1",
            task_id=str(uuid4()),
            evidence_type=ConnectorEvidenceType.DISCOVERY,
            status="discovered",
            executable_path="/usr/local/bin/cursor-agent",
            executable_fingerprint="abc",
            harness_version="2025.09.18",
            provider_mode=None,
            details={"execution_environment": "host"},
            created_at=utc_now(),
            expires_at=None,
        )
    )
    readiness = await store.get_readiness(node_id="node-1", connector_id="cursor")
    assert readiness.state is NodeConnectorState.AUTHENTICATION_REQUIRED
    await mesh.close()


async def test_mocked_node_lifecycle_e2e(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            readiness = await client.get("/nodes/node-1/connectors/gemini-cli/readiness")
            assert readiness.status_code == 200
            assert readiness.json()["state"] == "available_to_install"

            planned = await client.post(
                "/nodes/node-1/connectors/gemini-cli/install/plan",
                json={"method_id": "npm", "platform": "darwin"},
            )
            assert planned.status_code == 200
            body = planned.json()
            plan = body["plan"]
            assert body["approval_required"] is True

            store = mesh.connector_lifecycle.store
            async with mesh.database.sessions() as session:
                session.add(
                    NodeConnectorDiscoveryRow(
                        id=str(uuid4()),
                        node_id="node-1",
                        connector_id="gemini-cli",
                        connector_revision=plan["connector_revision"],
                        executable="/usr/local/bin/gemini",
                        version="0.1.0",
                        execution_environment="host",
                        discovered_at=utc_now(),
                    )
                )
                session.add(
                    NodeConnectorInstallationRow(
                        id=str(uuid4()),
                        node_id="node-1",
                        connector_id="gemini-cli",
                        connector_revision=plan["connector_revision"],
                        method_id="npm",
                        executable="/usr/local/bin/gemini",
                        version="0.1.0",
                        enabled_for_routing=False,
                        installed_at=utc_now(),
                    )
                )
                session.add(
                    NodeConnectorAuthenticationRow(
                        id=str(uuid4()),
                        node_id="node-1",
                        connector_id="gemini-cli",
                        method_id="google",
                        status="authenticated",
                        verified_at=utc_now(),
                        detail="ok",
                    )
                )
                session.add(
                    NodeConnectorCertificationRow(
                        id=str(uuid4()),
                        node_id="node-1",
                        connector_id="gemini-cli",
                        connector_revision=plan["connector_revision"],
                        harness_version="0.1.0",
                        executable_fingerprint="fp",
                        evidence_digest="digest",
                        passed_levels_json='{"read_test":true}',
                        certified_at=utc_now(),
                        expires_at=None,
                    )
                )
                await session.commit()
            await store.record_evidence(
                ConnectorEvidence(
                    evidence_id=str(uuid4()),
                    node_id="node-1",
                    connector_id="gemini-cli",
                    connector_revision=plan["connector_revision"],
                    task_id=str(uuid4()),
                    evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
                    status="passed",
                    executable_path="/usr/local/bin/gemini",
                    executable_fingerprint="fp",
                    harness_version="0.1.0",
                    provider_mode=None,
                    details={},
                    created_at=utc_now(),
                    expires_at=None,
                )
            )
            final = await client.get("/nodes/node-1/connectors/gemini-cli/readiness")
            assert final.json()["state"] == "routing_disabled"
            assert final.json()["recommended_action"] == "enable_routing"

    await mesh.close()


async def test_task_terminal_once_and_retry(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    await mesh.initialize()
    coordinator = mesh.connector_lifecycle
    coordinator._inline_node = False
    plan = await coordinator.persist_plan(
        mesh.plan_connector_task(
            node_id="node-1",
            connector_id="codex",
            action=ConnectorAction.DISCOVER,
            platform="darwin",
        )
    )
    task = await coordinator.approve_and_queue(
        plan.plan_id, plan_hash=plan.plan_hash, approved=True
    )
    assert task.status is ConnectorTaskStatus.QUEUED
    task = await coordinator.store.transition_task(
        task.task_id,
        expected_version=task.version,
        status=ConnectorTaskStatus.OFFERED_TO_NODE,
    )
    assert task.status is ConnectorTaskStatus.OFFERED_TO_NODE
    task = await coordinator.store.transition_task(
        task.task_id,
        expected_version=task.version,
        status=ConnectorTaskStatus.RUNNING,
        started_at=utc_now(),
    )
    await coordinator._finalize_task(task.task_id, ConnectorTaskStatus.FAILED, detail="boom")
    failed = await coordinator.get_task(task.task_id)
    assert failed.status is ConnectorTaskStatus.FAILED
    with pytest.raises(ValueError, match="already terminal"):
        await coordinator.store.transition_task(
            failed.task_id,
            expected_version=failed.version,
            status=ConnectorTaskStatus.SUCCEEDED,
        )
    retry = await coordinator.retry_task(failed.task_id)
    assert retry.previous_task_id == failed.task_id
    assert retry.task_id != failed.task_id
    await mesh.close()


async def test_cursor_advances_with_mocked_evidence(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'cursor.db'}")
    await mesh.initialize()
    store = mesh.connector_lifecycle.store
    revision = mesh.connector("cursor").revision
    await store.record_evidence(
        ConnectorEvidence(
            evidence_id=str(uuid4()),
            node_id="node-1",
            connector_id="cursor",
            connector_revision=revision,
            task_id=str(uuid4()),
            evidence_type=ConnectorEvidenceType.DISCOVERY,
            status="discovered",
            executable_path="/usr/local/bin/cursor-agent",
            executable_fingerprint="fp",
            harness_version="2025.09.18",
            provider_mode=None,
            details={},
            created_at=utc_now(),
            expires_at=None,
        )
    )
    assert (
        await store.get_readiness(node_id="node-1", connector_id="cursor")
    ).state is NodeConnectorState.AUTHENTICATION_REQUIRED
    await store.record_evidence(
        ConnectorEvidence(
            evidence_id=str(uuid4()),
            node_id="node-1",
            connector_id="cursor",
            connector_revision=revision,
            task_id=str(uuid4()),
            evidence_type=ConnectorEvidenceType.AUTHENTICATION,
            status="authenticated",
            executable_path="/usr/local/bin/cursor-agent",
            executable_fingerprint="fp",
            harness_version=None,
            provider_mode=None,
            details={"method_id": "cursor"},
            created_at=utc_now(),
            expires_at=None,
        )
    )
    assert (
        await store.get_readiness(node_id="node-1", connector_id="cursor")
    ).state is NodeConnectorState.VERIFICATION_REQUIRED
    await store.record_evidence(
        ConnectorEvidence(
            evidence_id=str(uuid4()),
            node_id="node-1",
            connector_id="cursor",
            connector_revision=revision,
            task_id=str(uuid4()),
            evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
            status="passed",
            executable_path="/usr/local/bin/cursor-agent",
            executable_fingerprint="fp",
            harness_version=None,
            provider_mode=None,
            details={},
            created_at=utc_now(),
            expires_at=None,
        )
    )
    assert (
        await store.get_readiness(node_id="node-1", connector_id="cursor")
    ).state is NodeConnectorState.CERTIFICATION_REQUIRED
    await store.record_evidence(
        ConnectorEvidence(
            evidence_id=str(uuid4()),
            node_id="node-1",
            connector_id="cursor",
            connector_revision=revision,
            task_id=str(uuid4()),
            evidence_type=ConnectorEvidenceType.CERTIFICATION,
            status="certified",
            executable_path="/usr/local/bin/cursor-agent",
            executable_fingerprint="fp",
            harness_version="2025.09.18",
            provider_mode=None,
            details={"passed_levels": {"read_test": True}, "evidence_digest": "x"},
            created_at=utc_now(),
            expires_at=None,
        )
    )
    readiness = await store.get_readiness(node_id="node-1", connector_id="cursor")
    assert readiness.state is NodeConnectorState.ROUTING_DISABLED
    async with mesh.database.sessions() as session:
        install = await session.scalar(
            select(NodeConnectorInstallationRow).where(
                NodeConnectorInstallationRow.node_id == "node-1",
                NodeConnectorInstallationRow.connector_id == "cursor",
            )
        )
        if install is None:
            session.add(
                NodeConnectorInstallationRow(
                    id=str(uuid4()),
                    node_id="node-1",
                    connector_id="cursor",
                    connector_revision=revision,
                    method_id="official",
                    executable="/usr/local/bin/cursor-agent",
                    version="2025.09.18",
                    enabled_for_routing=True,
                    installed_at=utc_now(),
                )
            )
        else:
            install.enabled_for_routing = True
        await session.commit()
    ready = await store.recompute(node_id="node-1", connector_id="cursor")
    assert ready.state is NodeConnectorState.READY
    await mesh.close()
