"""Production-path tests for authenticated node sessions and Cursor lifecycle."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature
from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.connectors.lifecycle_models import ConnectorTaskStatus
from joymesh.connectors.planning import ConnectorAction
from joymesh.control_plane.contracts import ConnectorTaskEnvelope, NodeRegistration
from joymesh.control_plane.journal import NodeTaskJournal
from joymesh.control_plane.security import (
    generate_node_keypair,
    inline_connector_node_enabled,
    sign_bytes,
    sign_connector_envelope,
    verify_bytes,
)
from joymesh.models import utc_now
from joymesh.service import JoyMesh


def test_challenge_signature_roundtrip() -> None:
    private_key, public_key = generate_node_keypair()
    challenge = "nonce-value"
    signature = sign_bytes(challenge.encode(), private_key)
    verify_bytes(challenge.encode(), signature, public_key)
    with pytest.raises(InvalidSignature):
        verify_bytes(b"other", signature, public_key)


def test_inline_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.delenv("JOYMESH_INLINE_CONNECTOR_NODE", raising=False)
    assert inline_connector_node_enabled() is False
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "1")
    with pytest.raises(RuntimeError, match="refused in production"):
        inline_connector_node_enabled()


def test_node_journal_survives_restart_and_blocks_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = NodeTaskJournal(path)
    entry = journal.accept(
        task_id="task-1",
        plan_hash="hash-1",
        connector_id="cursor",
        connector_revision="2026-07-29.1",
    )
    assert entry.status == "accepted"
    journal.mark_started("task-1", "hash-1")
    journal.mark_terminal(
        "task-1",
        "hash-1",
        status="succeeded",
        result_digest="digest",
        sequence=4,
    )
    restarted = NodeTaskJournal(path)
    stored = restarted.get("task-1", "hash-1")
    assert stored is not None
    assert stored.terminal_result_digest == "digest"
    assert (
        restarted.accept(
            task_id="task-1",
            plan_hash="hash-1",
            connector_id="cursor",
            connector_revision="2026-07-29.1",
        ).terminal_at
        is not None
    )


def test_connector_envelope_signing() -> None:
    private_key, public_key = generate_node_keypair()
    envelope = ConnectorTaskEnvelope(
        task_id=str(uuid4()),
        plan_id=str(uuid4()),
        node_id="node-1",
        connector_id="cursor",
        connector_revision="2026-07-29.1",
        action="discover",
        plan_hash="abc",
        executable="cursor-agent",
        method_id="discover",
        package_source="docs",
        key_id="k1",
        idempotency_key="idemp",
    )
    signed = sign_connector_envelope(envelope, private_key)
    from joymesh.control_plane.security import verify_connector_envelope

    verify_connector_envelope(signed, public_key)


async def test_queued_when_node_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "0")
    monkeypatch.setenv("JOYMESH_ENV", "development")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'offline.db'}")
    await mesh.initialize()
    mesh.connector_lifecycle._inline_node = False
    plan = await mesh.connector_lifecycle.persist_plan(
        mesh.plan_connector_task(
            node_id="node-1",
            connector_id="cursor",
            action=ConnectorAction.DISCOVER,
            platform="darwin",
        )
    )
    task = await mesh.connector_lifecycle.approve_and_queue(
        plan.plan_id, plan_hash=plan.plan_hash, approved=True
    )
    assert task.status is ConnectorTaskStatus.QUEUED
    await mesh.close()


async def test_mocked_cursor_production_path_with_inline_disabled_for_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "1")
    monkeypatch.setenv("JOYMESH_MOCK_CERTIFY", "1")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'cursor-prod.db'}")
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        store = mesh.connector_lifecycle.store
        revision = mesh.connector("cursor").revision
        from joymesh.connectors.lifecycle_models import ConnectorEvidence, ConnectorEvidenceType

        await store.record_evidence(
            ConnectorEvidence(
                evidence_id=str(uuid4()),
                node_id="node-1",
                connector_id="cursor",
                connector_revision=revision,
                task_id=str(uuid4()),
                evidence_type=ConnectorEvidenceType.DISCOVERY,
                status="discovered",
                executable_path="/Users/joytan/.local/bin/cursor-agent",
                executable_fingerprint="fp",
                harness_version="2025.09.18-7ae6800",
                provider_mode=None,
                details={},
                created_at=utc_now(),
                expires_at=None,
            )
        )
        await store.record_evidence(
            ConnectorEvidence(
                evidence_id=str(uuid4()),
                node_id="node-1",
                connector_id="cursor",
                connector_revision=revision,
                task_id=str(uuid4()),
                evidence_type=ConnectorEvidenceType.AUTHENTICATION,
                status="authenticated",
                executable_path="/Users/joytan/.local/bin/cursor-agent",
                executable_fingerprint="fp",
                harness_version=None,
                provider_mode=None,
                details={"method_id": "cursor"},
                created_at=utc_now(),
                expires_at=None,
            )
        )
        await store.record_evidence(
            ConnectorEvidence(
                evidence_id=str(uuid4()),
                node_id="node-1",
                connector_id="cursor",
                connector_revision=revision,
                task_id=str(uuid4()),
                evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
                status="passed",
                executable_path="/Users/joytan/.local/bin/cursor-agent",
                executable_fingerprint="fp",
                harness_version=None,
                provider_mode=None,
                details={},
                created_at=utc_now(),
                expires_at=None,
            )
        )
        await store.record_evidence(
            ConnectorEvidence(
                evidence_id=str(uuid4()),
                node_id="node-1",
                connector_id="cursor",
                connector_revision=revision,
                task_id=str(uuid4()),
                evidence_type=ConnectorEvidenceType.CERTIFICATION,
                status="certified",
                executable_path="/Users/joytan/.local/bin/cursor-agent",
                executable_fingerprint="fp",
                harness_version="2025.09.18-7ae6800",
                provider_mode=None,
                details={
                    "passed_levels": {
                        "read_only_certified": True,
                        "write_certified": False,
                        "command_certified": False,
                        "session_resume_certified": False,
                    },
                    "evidence_digest": "x",
                    "routing_profile": "cursor_read_only",
                },
                created_at=utc_now(),
                expires_at=None,
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            readiness = await client.get("/nodes/node-1/connectors/cursor/readiness")
            assert readiness.json()["state"] == "routing_disabled"
            enabled = await client.post("/nodes/node-1/connectors/cursor/routing/enable")
            assert enabled.status_code == 200
            assert enabled.json()["state"] == "ready"
            assert enabled.json()["routing_eligible"] is True
    await mesh.close()


async def test_gateway_registers_node_for_session_lookup(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'session.db'}")
    private_key, public_key = generate_node_keypair()
    mesh.control_plane.store.nodes["node-1"] = NodeRegistration(
        id="node-1",
        organisation_id="org",
        workspace_id="ws",
        name="Mac",
        public_key=public_key,
        key_id="k1",
        platform="darwin",
        version="0.1.0",
    )
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/nodes/node-1/session")
            assert response.status_code == 200
            assert response.json() is None
    del private_key
    await mesh.close()
