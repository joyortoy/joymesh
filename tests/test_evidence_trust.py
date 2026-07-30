"""Evidence trust, execution origin, auth parsing, and production routing guards."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from joymesh.connectors.lifecycle_models import (
    ConnectorEvidence,
    ConnectorEvidenceType,
    ConnectorExecutionOrigin,
    EvidenceTrustLevel,
    NodeConnectorState,
)
from joymesh.connectors.node_runner import parse_cursor_auth_status
from joymesh.connectors.readiness import ConnectorReadinessService, _NodeConnectorSnapshot
from joymesh.control_plane.security import assert_live_production_config, mock_certify_enabled
from joymesh.models import utc_now
from joymesh.service import JoyMesh


def test_parse_cursor_auth_authenticated() -> None:
    assert parse_cursor_auth_status("Login successful!\nLogged in\n", returncode=0) is True


def test_parse_cursor_auth_unauthenticated() -> None:
    assert parse_cursor_auth_status("Not logged in\n", returncode=0) is False
    assert parse_cursor_auth_status("Logged in", returncode=1) is False


def test_assert_live_production_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "0")
    monkeypatch.delenv("JOYMESH_MOCK_CERTIFY", raising=False)
    assert assert_live_production_config()["inline_enabled"] is False
    monkeypatch.setenv("JOYMESH_MOCK_CERTIFY", "1")
    with pytest.raises(RuntimeError, match="MOCK_CERTIFY"):
        assert_live_production_config()
    monkeypatch.delenv("JOYMESH_MOCK_CERTIFY", raising=False)
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "1")
    with pytest.raises(RuntimeError, match="INLINE"):
        assert_live_production_config()


def test_mock_certify_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOYMESH_MOCK_CERTIFY", raising=False)
    assert mock_certify_enabled() is False
    monkeypatch.setenv("JOYMESH_MOCK_CERTIFY", "1")
    assert mock_certify_enabled() is True


def _snapshot(**overrides: object) -> _NodeConnectorSnapshot:
    from joymesh.connectors import ConnectorCatalogue

    revision = ConnectorCatalogue.builtins().get("cursor").revision
    base = _NodeConnectorSnapshot(
        discovery_executable="/bin/cursor-agent",
        discovery_version="2025.09.18-7ae6800",
        discovery_revision=revision,
        discovery_environment="host",
        installation_executable="/bin/cursor-agent",
        installation_version="2025.09.18-7ae6800",
        installation_revision=revision,
        installation_routing_enabled=True,
        auth_status="authenticated",
        auth_verified_at=utc_now(),
        auth_method_id="cursor",
        certification_valid=True,
        certification_expires_at=None,
        evidence_by_type={
            ConnectorEvidenceType.AUTHENTICATION: {
                "status": "authenticated",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "connector_revision": revision,
            },
            ConnectorEvidenceType.ADAPTER_CONFORMANCE: {
                "status": "passed",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "connector_revision": revision,
            },
            ConnectorEvidenceType.CERTIFICATION: {
                "status": "certified",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "routing_profile": "read_only_repository",
                "connector_revision": revision,
            },
        },
        active_task_status=None,
        active_task_id=None,
        active_task_action=None,
        latest_evidence_id="e1",
        executable_fingerprint="fp",
        platform="darwin",
        node_online=True,
    )
    return _NodeConnectorSnapshot(**{**base.__dict__, **overrides})


def test_production_routing_rejects_mock_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    from joymesh.connectors import ConnectorCatalogue

    revision = ConnectorCatalogue.builtins().get("cursor").revision
    service = ConnectorReadinessService()
    snapshot = _snapshot(
        evidence_by_type={
            ConnectorEvidenceType.AUTHENTICATION: {
                "status": "authenticated",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "connector_revision": revision,
            },
            ConnectorEvidenceType.ADAPTER_CONFORMANCE: {
                "status": "passed",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "connector_revision": revision,
            },
            ConnectorEvidenceType.CERTIFICATION: {
                "status": "certified",
                "trust_level": EvidenceTrustLevel.MOCK.value,
                "execution_origin": ConnectorExecutionOrigin.MOCK_TEST.value,
                "routing_profile": "read_only_repository",
                "connector_revision": revision,
            },
        }
    )
    readiness = service.derive_from_snapshot(node_id="n1", connector_id="cursor", snapshot=snapshot)
    assert readiness.state is NodeConnectorState.CERTIFICATION_REQUIRED
    assert readiness.routing_eligible is False


def test_production_routing_rejects_inline_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    from joymesh.connectors import ConnectorCatalogue

    revision = ConnectorCatalogue.builtins().get("cursor").revision
    service = ConnectorReadinessService()
    snapshot = _snapshot(
        evidence_by_type={
            ConnectorEvidenceType.AUTHENTICATION: {
                "status": "authenticated",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "connector_revision": revision,
            },
            ConnectorEvidenceType.ADAPTER_CONFORMANCE: {
                "status": "passed",
                "trust_level": EvidenceTrustLevel.NODE_ATTESTED.value,
                "execution_origin": ConnectorExecutionOrigin.REMOTE_NODE.value,
                "connector_revision": revision,
            },
            ConnectorEvidenceType.CERTIFICATION: {
                "status": "certified",
                "trust_level": EvidenceTrustLevel.DEVELOPMENT.value,
                "execution_origin": ConnectorExecutionOrigin.INLINE_DEVELOPMENT.value,
                "routing_profile": "read_only_repository",
                "connector_revision": revision,
            },
        }
    )
    readiness = service.derive_from_snapshot(node_id="n1", connector_id="cursor", snapshot=snapshot)
    assert readiness.state is NodeConnectorState.CERTIFICATION_REQUIRED


def test_production_accepts_node_attested_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    service = ConnectorReadinessService()
    readiness = service.derive_from_snapshot(
        node_id="n1", connector_id="cursor", snapshot=_snapshot()
    )
    assert readiness.state is NodeConnectorState.READY
    assert readiness.routing_profile == "read_only_repository"
    assert readiness.evidence_trust_level is EvidenceTrustLevel.NODE_ATTESTED


async def test_enable_routing_rejects_mock_in_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "0")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'trust.db'}")
    await mesh.initialize()
    store = mesh.connector_lifecycle.store
    revision = mesh.connector("cursor").revision
    for kind, status, trust, origin in (
        (
            ConnectorEvidenceType.DISCOVERY,
            "discovered",
            EvidenceTrustLevel.NODE_ATTESTED,
            ConnectorExecutionOrigin.REMOTE_NODE,
        ),
        (
            ConnectorEvidenceType.AUTHENTICATION,
            "authenticated",
            EvidenceTrustLevel.NODE_ATTESTED,
            ConnectorExecutionOrigin.REMOTE_NODE,
        ),
        (
            ConnectorEvidenceType.ADAPTER_CONFORMANCE,
            "passed",
            EvidenceTrustLevel.NODE_ATTESTED,
            ConnectorExecutionOrigin.REMOTE_NODE,
        ),
        (
            ConnectorEvidenceType.CERTIFICATION,
            "certified",
            EvidenceTrustLevel.MOCK,
            ConnectorExecutionOrigin.MOCK_TEST,
        ),
    ):
        await store.record_evidence(
            ConnectorEvidence(
                evidence_id=str(uuid4()),
                node_id="node-1",
                connector_id="cursor",
                connector_revision=revision,
                task_id=str(uuid4()),
                evidence_type=kind,
                status=status,
                executable_path="/tmp/cursor-agent",
                executable_fingerprint="fp",
                harness_version="2025.09.18-7ae6800",
                provider_mode=None,
                details={"routing_profile": "read_only_repository", "method_id": "cursor"},
                created_at=utc_now(),
                expires_at=None,
                trust_level=trust,
                execution_origin=origin,
            )
        )
    readiness = await mesh.connector_lifecycle.get_readiness(
        node_id="node-1", connector_id="cursor"
    )
    assert readiness.state is NodeConnectorState.CERTIFICATION_REQUIRED
    with pytest.raises(PermissionError):
        await mesh.connector_lifecycle.enable_routing(node_id="node-1", connector_id="cursor")
    await mesh.close()
