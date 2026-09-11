"""Test that node gateway sessions register with RuntimeService."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.types import ASGIApp, Receive, Scope, Send

from joymesh.api import create_app
from joymesh.control_plane.contracts import NodeProtocolMessageType, ProtocolMessage
from joymesh.control_plane.security import generate_node_keypair, sign_bytes
from joymesh.service import JoyMesh


class _ASGIWebSocket:
    """Drive a FastAPI websocket endpoint on the current asyncio loop."""

    def __init__(self, app: ASGIApp, path: str = "/nodes/connect") -> None:
        self._app = app
        self._path = path
        self._incoming: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _ASGIWebSocket:
        scope: Scope = {
            "type": "websocket",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
            "extensions": {},
        }

        async def receive() -> Mapping[str, Any]:
            return await self._incoming.get()

        async def send(message: Mapping[str, Any]) -> None:
            await self._outgoing.put(message)

        await self._incoming.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(self._run(scope, receive, send))
        message = await self._outgoing.get()
        if message["type"] != "websocket.accept":
            raise AssertionError(f"expected websocket.accept, got {message!r}")
        return self

    async def _run(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._app(scope, receive, send)

    async def send_json(self, payload: Mapping[str, Any]) -> None:
        await self._incoming.put(
            {"type": "websocket.receive", "text": json.dumps(payload)}
        )

    async def receive_text(self) -> str:
        while True:
            message = await self._outgoing.get()
            if message["type"] == "websocket.send":
                text = message.get("text")
                if text is not None:
                    return str(text)
                raw = message.get("bytes")
                if raw is not None:
                    return bytes(raw).decode()
            if message["type"] == "websocket.close":
                raise AssertionError(f"websocket closed: {message!r}")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._incoming.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()


async def _authenticate_node(ws: _ASGIWebSocket, node_id: str, private_key: str) -> None:
    hello = ProtocolMessage(
        type=NodeProtocolMessageType.HELLO,
        node_id=node_id,
        sequence=0,
        payload={"runtime_version": "0.1.0"},
    )
    await ws.send_json(hello.model_dump(mode="json"))
    challenge_msg = ProtocolMessage.model_validate_json(await ws.receive_text())
    assert challenge_msg.type is NodeProtocolMessageType.CHALLENGE
    challenge = challenge_msg.payload["challenge"]
    signature = sign_bytes(challenge.encode(), private_key)
    auth = ProtocolMessage(
        type=NodeProtocolMessageType.AUTHENTICATE,
        node_id=node_id,
        sequence=1,
        payload={"challenge": challenge, "signature": signature},
    )
    await ws.send_json(auth.model_dump(mode="json"))
    session_msg = ProtocolMessage.model_validate_json(await ws.receive_text())
    assert session_msg.type is NodeProtocolMessageType.SESSION_ESTABLISHED


async def _wait_connected(mesh: JoyMesh, expected: int) -> None:
    for _ in range(200):
        if mesh.runtime_service.metrics.connected_nodes == expected:
            return
        await asyncio.sleep(0.01)
    assert mesh.runtime_service.metrics.connected_nodes == expected


@pytest.mark.asyncio
async def test_gateway_connection_increments_connected_nodes(tmp_path: Path) -> None:
    """When a node connects via gateway, RuntimeService.metrics.connected_nodes increments."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    await mesh.initialize()

    assert mesh.runtime_service.metrics.connected_nodes == 0

    private_key, public_key = generate_node_keypair()
    pairing, device_code = await mesh.control_plane.begin_pairing(
        organisation_id="org1",
        workspace_id="ws1",
        code_challenge="challenge",
    )
    await mesh.control_plane.approve_pairing(pairing.id, user_id="user1")
    node = await mesh.control_plane.register_node(
        pairing.id,
        device_code=device_code,
        name="TestNode",
        public_key=public_key,
        key_id="test-key",
        platform="darwin",
        version="0.1.0",
    )
    node_id = node.id

    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        ConnectorReadiness,
        EvidenceTrustLevel,
        NodeConnectorState,
        RecommendedConnectorAction,
    )

    readiness = ConnectorReadiness(
        node_id=node_id,
        connector_id="cursor",
        state=NodeConnectorState.READY,
        routing_eligible=True,
        catalogue_maturity="stable",
        installed_version="1.0.0",
        evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
        execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
        recommended_action=RecommendedConnectorAction.NONE,
    )
    await mesh.connector_lifecycle.store.save_readiness(readiness)

    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/ready")
            assert ready.status_code == 200
            assert ready.json()["connected_nodes"] == 0
            assert ready.json()["ready"] is True

            async with _ASGIWebSocket(app) as ws:
                await _authenticate_node(ws, node_id, private_key)
                await _wait_connected(mesh, 1)
                ready = await client.get("/ready")
                assert ready.status_code == 200
                assert ready.json()["connected_nodes"] == 1

            await _wait_connected(mesh, 0)
            ready = await client.get("/ready")
            assert ready.status_code == 200
            assert ready.json()["connected_nodes"] == 0


@pytest.mark.asyncio
async def test_multiple_nodes_increment_counter_correctly(tmp_path: Path) -> None:
    """Multiple node connections should each increment the counter."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'multi.db'}")
    await mesh.initialize()

    assert mesh.runtime_service.metrics.connected_nodes == 0

    nodes = []
    for i in range(2):
        private_key, public_key = generate_node_keypair()
        pairing, device_code = await mesh.control_plane.begin_pairing(
            organisation_id="org1",
            workspace_id=f"ws{i}",
            code_challenge=f"challenge{i}",
        )
        await mesh.control_plane.approve_pairing(pairing.id, user_id=f"user{i}")
        node = await mesh.control_plane.register_node(
            pairing.id,
            device_code=device_code,
            name=f"Node{i}",
            public_key=public_key,
            key_id=f"key{i}",
            platform="darwin",
            version="0.1.0",
        )
        nodes.append((node.id, private_key))

        from joymesh.connectors.lifecycle_models import (
            ConnectorExecutionOrigin,
            ConnectorReadiness,
            EvidenceTrustLevel,
            NodeConnectorState,
            RecommendedConnectorAction,
        )

        readiness = ConnectorReadiness(
            node_id=node.id,
            connector_id="cursor",
            state=NodeConnectorState.READY,
            routing_eligible=True,
            catalogue_maturity="stable",
            installed_version="1.0.0",
            evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
            execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
            recommended_action=RecommendedConnectorAction.NONE,
        )
        await mesh.connector_lifecycle.store.save_readiness(readiness)

    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with _ASGIWebSocket(app) as ws1:
                node_id1, private_key1 = nodes[0]
                await _authenticate_node(ws1, node_id1, private_key1)
                await _wait_connected(mesh, 1)

                async with _ASGIWebSocket(app) as ws2:
                    node_id2, private_key2 = nodes[1]
                    await _authenticate_node(ws2, node_id2, private_key2)
                    await _wait_connected(mesh, 2)
                    ready = await client.get("/ready")
                    assert ready.json()["connected_nodes"] == 2

                await _wait_connected(mesh, 1)

            await _wait_connected(mesh, 0)


@pytest.mark.asyncio
async def test_build_snapshot_with_real_readiness_fields(tmp_path: Path) -> None:
    """Verify _build_node_snapshot doesn't crash with actual ConnectorReadiness fields."""
    from joymesh.api import _build_node_snapshot
    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        ConnectorReadiness,
        EvidenceTrustLevel,
        NodeConnectorState,
        RecommendedConnectorAction,
    )

    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'snapshot.db'}")
    await mesh.initialize()

    _private_key, public_key = generate_node_keypair()
    pairing, device_code = await mesh.control_plane.begin_pairing(
        organisation_id="org1",
        workspace_id="ws1",
        code_challenge="challenge",
    )
    await mesh.control_plane.approve_pairing(pairing.id, user_id="user1")
    node = await mesh.control_plane.register_node(
        pairing.id,
        device_code=device_code,
        name="TestNode",
        public_key=public_key,
        key_id="test-key",
        platform="darwin",
        version="0.1.0",
    )

    readiness = ConnectorReadiness(
        node_id=node.id,
        connector_id="cursor",
        state=NodeConnectorState.READY,
        routing_eligible=True,
        catalogue_maturity="stable",
        installed_version="1.0.0",
        executable_path="/usr/local/bin/cursor",
        routing_profile="read_only",
        evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
        execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
        recommended_action=RecommendedConnectorAction.NONE,
        blocking_reason=None,
        active_task_id=None,
        latest_evidence_id=None,
    )
    await mesh.connector_lifecycle.store.save_readiness(readiness)

    snapshot = await _build_node_snapshot(mesh, node_id=node.id, online=True)

    assert snapshot.node_id == node.id
    assert snapshot.online is True
    assert "cursor" in snapshot.connectors

    connector = snapshot.connectors["cursor"]
    assert connector.connector_id == "cursor"
    assert connector.installed is True
    assert connector.readiness == NodeConnectorState.READY
    assert connector.authenticated is True
    assert connector.routing_enabled is True
    assert isinstance(connector.certified_capabilities, frozenset)
    assert connector.trust_level == EvidenceTrustLevel.NODE_ATTESTED
    assert connector.execution_origin == ConnectorExecutionOrigin.REMOTE_NODE


@pytest.mark.asyncio
async def test_reconnect_does_not_double_count(tmp_path: Path) -> None:
    """Reconnecting the same node should not double-count."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'reconnect.db'}")
    await mesh.initialize()

    private_key, public_key = generate_node_keypair()
    pairing, device_code = await mesh.control_plane.begin_pairing(
        organisation_id="org1",
        workspace_id="ws1",
        code_challenge="challenge",
    )
    await mesh.control_plane.approve_pairing(pairing.id, user_id="user1")
    node = await mesh.control_plane.register_node(
        pairing.id,
        device_code=device_code,
        name="TestNode",
        public_key=public_key,
        key_id="test-key",
        platform="darwin",
        version="0.1.0",
    )

    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        ConnectorReadiness,
        EvidenceTrustLevel,
        NodeConnectorState,
        RecommendedConnectorAction,
    )

    readiness = ConnectorReadiness(
        node_id=node.id,
        connector_id="cursor",
        state=NodeConnectorState.READY,
        routing_eligible=True,
        catalogue_maturity="stable",
        installed_version="1.0.0",
        evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
        execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
        recommended_action=RecommendedConnectorAction.NONE,
    )
    await mesh.connector_lifecycle.store.save_readiness(readiness)

    app = create_app(mesh)

    async def connect_node() -> None:
        async with _ASGIWebSocket(app) as ws:
            await _authenticate_node(ws, node.id, private_key)
            await _wait_connected(mesh, 1)

    async with app.router.lifespan_context(app):
        await connect_node()
        await _wait_connected(mesh, 0)
        await connect_node()
        await _wait_connected(mesh, 0)
        assert mesh.runtime_service.metrics.connected_nodes == 0
