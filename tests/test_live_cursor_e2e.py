"""Optional real Cursor + remote-node live E2E (skipped unless JOYMESH_LIVE_CURSOR=1)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from httpx import AsyncClient

from joymesh.api import create_app
from joymesh.connectors.lifecycle_models import (
    ConnectorExecutionOrigin,
    EvidenceTrustLevel,
    NodeConnectorState,
)
from joymesh.control_plane.contracts import NodeRegistration
from joymesh.control_plane.journal import NodeTaskJournal
from joymesh.control_plane.node import JoyMeshNode
from joymesh.control_plane.security import (
    assert_live_production_config,
    generate_node_keypair,
)
from joymesh.service import JoyMesh

pytestmark = pytest.mark.skipif(
    os.environ.get("JOYMESH_LIVE_CURSOR") != "1",
    reason="Set JOYMESH_LIVE_CURSOR=1 to run the real Cursor production E2E",
)


async def test_real_cursor_remote_node_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.setenv("JOYMESH_INLINE_CONNECTOR_NODE", "0")
    monkeypatch.delenv("JOYMESH_MOCK_CERTIFY", raising=False)
    config = assert_live_production_config()
    assert config["inline_enabled"] is False

    private_key, public_key = generate_node_keypair()
    node_id = f"live-cursor-{uuid4().hex[:8]}"
    db = tmp_path / "live.db"
    journal = NodeTaskJournal(tmp_path / "journal.sqlite3")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{db}")
    await mesh.initialize()
    mesh.control_plane.store.nodes[node_id] = NodeRegistration(
        id=node_id,
        organisation_id="org",
        workspace_id="ws",
        name="Live Mac",
        public_key=public_key,
        key_id="k1",
        platform="darwin",
        version="0.1.0",
    )
    mesh.connector_lifecycle._inline_node = False
    app = create_app(mesh)

    config_uv = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config_uv)
    serve_task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://localhost:{port}"
    gateway_url = f"ws://localhost:{port}/nodes/connect"

    node = JoyMeshNode(
        node_id=node_id,
        gateway_url=gateway_url,
        private_key=private_key,
        journal=journal,
    )
    node_task = asyncio.create_task(node.run())
    try:
        async with AsyncClient(base_url=base, timeout=180.0) as client:
            session = None
            for _ in range(40):
                response = await client.get(f"/nodes/{node_id}/session")
                session = response.json()
                if session and session.get("status") == "online":
                    break
                await asyncio.sleep(0.25)
            assert session and session.get("status") == "online", (
                f"node failed to authenticate: {session}"
            )
            session_id = session["session_id"]

            async def run_action(action: str) -> dict:
                path = {
                    "discover": f"/nodes/{node_id}/connectors/cursor/discover/plan",
                    "verify_authentication": (
                        f"/nodes/{node_id}/connectors/cursor/verify-authentication/plan"
                    ),
                    "verify_adapter": (f"/nodes/{node_id}/connectors/cursor/verify-adapter/plan"),
                    "certify": f"/nodes/{node_id}/connectors/cursor/certify/plan",
                }[action]
                planned = await client.post(path, json={"platform": "darwin"})
                assert planned.status_code == 200, planned.text
                plan = planned.json()["plan"]
                executed = await client.post(
                    f"/connector-tasks/{plan['plan_id']}/execute",
                    json={"plan_hash": plan["plan_hash"], "approved": True},
                )
                assert executed.status_code == 200, executed.text
                task = executed.json()
                body = task
                for _ in range(360):
                    current = await client.get(f"/connector-tasks/{task['task_id']}")
                    body = current.json()
                    if body["status"] in {
                        "succeeded",
                        "failed",
                        "cancelled",
                        "waiting_for_user",
                    }:
                        return body
                    await asyncio.sleep(0.5)
                return body

            discover = await run_action("discover")
            assert discover["status"] == "succeeded", discover

            auth = await run_action("verify_authentication")
            assert auth["status"] == "succeeded", auth

            adapter = await run_action("verify_adapter")
            assert adapter["status"] == "succeeded", adapter

            certify = await run_action("certify")
            assert certify["status"] == "succeeded", certify

            readiness = await client.get(f"/nodes/{node_id}/connectors/cursor/readiness")
            body = readiness.json()
            assert body["state"] == NodeConnectorState.ROUTING_DISABLED.value, body
            assert body["evidence_trust_level"] == EvidenceTrustLevel.NODE_ATTESTED.value
            assert body["execution_origin"] == ConnectorExecutionOrigin.REMOTE_NODE.value

            enabled = await client.post(f"/nodes/{node_id}/connectors/cursor/routing/enable")
            assert enabled.status_code == 200, enabled.text
            final = enabled.json()
            assert final["state"] == NodeConnectorState.READY.value
            assert final["routing_profile"] == "read_only_repository"
            assert final["routing_eligible"] is True
            assert session_id
    finally:
        await node.stop()
        node_task.cancel()
        server.should_exit = True
        await asyncio.gather(serve_task, node_task, return_exceptions=True)
        await mesh.close()
