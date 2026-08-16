"""Test that node gateway sessions register with RuntimeService."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import WebSocketTestSession

from joymesh.api import create_app
from joymesh.control_plane.contracts import NodeProtocolMessageType, ProtocolMessage
from joymesh.control_plane.security import generate_node_keypair, sign_bytes
from joymesh.service import JoyMesh


@pytest.mark.asyncio
async def test_gateway_connection_increments_connected_nodes(tmp_path: Path) -> None:
    """When a node connects via gateway, RuntimeService.metrics.connected_nodes increments."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    await mesh.initialize()
    
    # Start with 0 connected nodes
    assert mesh.runtime_service.metrics.connected_nodes == 0
    
    # Set up control plane with a registered node
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
    
    # Add a connector for this node so it can route
    from joymesh.connectors.lifecycle_models import ConnectorReadiness, NodeConnectorState
    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        EvidenceTrustLevel,
    )
    
    readiness = ConnectorReadiness(
        node_id=node_id,
        connector_id="cursor",
        installed=True,
        state=NodeConnectorState.READY,
        authenticated=True,
        routing_enabled=True,
        certified_capabilities=("repository.read",),
        evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
        evidence_execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
    )
    await mesh.connector_lifecycle.store.save_readiness(readiness)
    
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Check /ready before connection
            ready = await client.get("/ready")
            assert ready.status_code == 200
            assert ready.json()["connected_nodes"] == 0
            assert ready.json()["ready"] is True
            
            # Connect via websocket
            with client.websocket_connect("/nodes/connect") as ws:
                # Send HELLO
                hello = ProtocolMessage(
                    type=NodeProtocolMessageType.HELLO,
                    node_id=node_id,
                    sequence=0,
                    payload={"runtime_version": "0.1.0"},
                )
                ws.send_json(hello.model_dump(mode="json"))
                
                # Receive CHALLENGE
                challenge_msg = ProtocolMessage.model_validate_json(ws.receive_text())
                assert challenge_msg.type is NodeProtocolMessageType.CHALLENGE
                challenge = challenge_msg.payload["challenge"]
                
                # Send signed AUTHENTICATE
                signature = sign_bytes(challenge.encode(), private_key)
                auth = ProtocolMessage(
                    type=NodeProtocolMessageType.AUTHENTICATE,
                    node_id=node_id,
                    sequence=1,
                    payload={"challenge": challenge, "signature": signature},
                )
                ws.send_json(auth.model_dump(mode="json"))
                
                # Receive SESSION_ESTABLISHED
                session_msg = ProtocolMessage.model_validate_json(ws.receive_text())
                assert session_msg.type is NodeProtocolMessageType.SESSION_ESTABLISHED
                
                # Check metrics after connection
                assert mesh.runtime_service.metrics.connected_nodes == 1
                
                # Check /ready during connection
                ready = await client.get("/ready")
                assert ready.status_code == 200
                assert ready.json()["connected_nodes"] == 1
                
            # After websocket closes, check metrics
            assert mesh.runtime_service.metrics.connected_nodes == 0
            
            # Check /ready after disconnection
            ready = await client.get("/ready")
            assert ready.status_code == 200
            assert ready.json()["connected_nodes"] == 0


@pytest.mark.asyncio
async def test_multiple_nodes_increment_counter_correctly(tmp_path: Path) -> None:
    """Multiple node connections should each increment the counter."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'multi.db'}")
    await mesh.initialize()
    
    assert mesh.runtime_service.metrics.connected_nodes == 0
    
    # Register two nodes
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
        
        # Add connector readiness
        from joymesh.connectors.lifecycle_models import (
            ConnectorReadiness,
            NodeConnectorState,
            ConnectorExecutionOrigin,
            EvidenceTrustLevel,
        )
        
        readiness = ConnectorReadiness(
            node_id=node.id,
            connector_id="cursor",
            installed=True,
            state=NodeConnectorState.READY,
            authenticated=True,
            routing_enabled=True,
            certified_capabilities=("repository.read",),
            evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
            evidence_execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
        )
        await mesh.connector_lifecycle.store.save_readiness(readiness)
    
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Connect first node
            with client.websocket_connect("/nodes/connect") as ws1:
                node_id1, private_key1 = nodes[0]
                hello = ProtocolMessage(
                    type=NodeProtocolMessageType.HELLO,
                    node_id=node_id1,
                    sequence=0,
                    payload={"runtime_version": "0.1.0"},
                )
                ws1.send_json(hello.model_dump(mode="json"))
                challenge_msg = ProtocolMessage.model_validate_json(ws1.receive_text())
                challenge = challenge_msg.payload["challenge"]
                signature = sign_bytes(challenge.encode(), private_key1)
                auth = ProtocolMessage(
                    type=NodeProtocolMessageType.AUTHENTICATE,
                    node_id=node_id1,
                    sequence=1,
                    payload={"challenge": challenge, "signature": signature},
                )
                ws1.send_json(auth.model_dump(mode="json"))
                ws1.receive_text()  # SESSION_ESTABLISHED
                
                # Should have 1 connected
                assert mesh.runtime_service.metrics.connected_nodes == 1
                
                # Connect second node
                with client.websocket_connect("/nodes/connect") as ws2:
                    node_id2, private_key2 = nodes[1]
                    hello = ProtocolMessage(
                        type=NodeProtocolMessageType.HELLO,
                        node_id=node_id2,
                        sequence=0,
                        payload={"runtime_version": "0.1.0"},
                    )
                    ws2.send_json(hello.model_dump(mode="json"))
                    challenge_msg = ProtocolMessage.model_validate_json(ws2.receive_text())
                    challenge = challenge_msg.payload["challenge"]
                    signature = sign_bytes(challenge.encode(), private_key2)
                    auth = ProtocolMessage(
                        type=NodeProtocolMessageType.AUTHENTICATE,
                        node_id=node_id2,
                        sequence=1,
                        payload={"challenge": challenge, "signature": signature},
                    )
                    ws2.send_json(auth.model_dump(mode="json"))
                    ws2.receive_text()  # SESSION_ESTABLISHED
                    
                    # Should have 2 connected
                    assert mesh.runtime_service.metrics.connected_nodes == 2
                    
                    # Check /ready
                    ready = await client.get("/ready")
                    assert ready.json()["connected_nodes"] == 2
                
                # After ws2 closes, should have 1
                assert mesh.runtime_service.metrics.connected_nodes == 1
            
            # After both close, should have 0
            assert mesh.runtime_service.metrics.connected_nodes == 0


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
        ConnectorReadiness,
        NodeConnectorState,
        ConnectorExecutionOrigin,
        EvidenceTrustLevel,
    )
    
    readiness = ConnectorReadiness(
        node_id=node.id,
        connector_id="cursor",
        installed=True,
        state=NodeConnectorState.READY,
        authenticated=True,
        routing_enabled=True,
        certified_capabilities=("repository.read",),
        evidence_trust_level=EvidenceTrustLevel.NODE_ATTESTED,
        evidence_execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
    )
    await mesh.connector_lifecycle.store.save_readiness(readiness)
    
    app = create_app(mesh)
    
    async def connect_node() -> None:
        """Helper to connect and authenticate a node."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with client.websocket_connect("/nodes/connect") as ws:
                hello = ProtocolMessage(
                    type=NodeProtocolMessageType.HELLO,
                    node_id=node.id,
                    sequence=0,
                    payload={"runtime_version": "0.1.0"},
                )
                ws.send_json(hello.model_dump(mode="json"))
                challenge_msg = ProtocolMessage.model_validate_json(ws.receive_text())
                challenge = challenge_msg.payload["challenge"]
                signature = sign_bytes(challenge.encode(), private_key)
                auth = ProtocolMessage(
                    type=NodeProtocolMessageType.AUTHENTICATE,
                    node_id=node.id,
                    sequence=1,
                    payload={"challenge": challenge, "signature": signature},
                )
                ws.send_json(auth.model_dump(mode="json"))
                ws.receive_text()  # SESSION_ESTABLISHED
                
                assert mesh.runtime_service.metrics.connected_nodes == 1
    
    async with app.router.lifespan_context(app):
        # Connect once
        await connect_node()
        assert mesh.runtime_service.metrics.connected_nodes == 0
        
        # Reconnect
        await connect_node()
        assert mesh.runtime_service.metrics.connected_nodes == 0
        
        # Should still be 0, not 2
        assert mesh.runtime_service.metrics.connected_nodes == 0
