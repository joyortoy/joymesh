"""Tests for JoyCLI compatibility routes."""

from pathlib import Path

from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.runtime_v1.models import RuntimeTaskStatus
from joymesh.service import JoyMesh


async def test_ready_endpoint(tmp_path: Path) -> None:
    """Test GET /ready returns readiness information."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)
    
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["ready"] is True
            assert data["status"] == "ok"
            assert "routes" in data
            assert data["routes"]["executions"] == "/executions"
            assert "connected_nodes" in data
            assert "queued_tasks" in data


async def test_create_execution(tmp_path: Path) -> None:
    """Test POST /executions creates a task and returns execution_id."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)
    
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_123",
                    "step_id": "step_001",
                    "repository_path": str(tmp_path),
                    "instruction": "Write tests for the authentication module",
                    "policy_grant": "read_only",
                    "capabilities": ["repository.read", "filesystem.read"],
                    "timeout_seconds": 300,
            },
        )
        if response.status_code != 200:
            print(f"Error: {response.json()}")
        assert response.status_code == 200
        data = response.json()
        assert "execution_id" in data
        execution_id = data["execution_id"]
        
        # Verify the task was created in the runtime
        task = await mesh.runtime_service.store.get_task(execution_id)
        assert task.task_id == execution_id
        assert task.workspace_id == str(tmp_path)
        # Task should not be rejected
        assert task.status != RuntimeTaskStatus.REJECTED


async def test_execution_events(tmp_path: Path) -> None:
    """Test GET /executions/{id}/events returns normalized events."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)
    
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create an execution
            create_response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_456",
                    "step_id": "step_002",
                    "repository_path": str(tmp_path),
                    "instruction": "Refactor the database layer",
                    "policy_grant": "read_only",
                    "capabilities": ["repository.read"],
                },
            )
            execution_id = create_response.json()["execution_id"]
            
            # Get events
            events_response = await client.get(f"/executions/{execution_id}/events")
            assert events_response.status_code == 200
            data = events_response.json()
            assert "events" in data
            assert isinstance(data["events"], list)
            
            # Should have at least one event (status-based synthetic event)
            assert len(data["events"]) > 0
            
            # Verify event structure
            for event in data["events"]:
                assert "event_type" in event
                assert event["event_type"] in [
                    "accepted",
                    "queued",
                    "started",
                    "output",
                    "tool",
                    "file",
                    "evidence",
                    "blocked",
                    "failed",
                    "cancelled",
                    "completed",
                    "usage",
                    "fallback",
                ]


async def test_cancel_execution(tmp_path: Path) -> None:
    """Test POST /executions/{id}/cancel cancels a task."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)
    
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create an execution
            create_response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_789",
                    "step_id": "step_003",
                    "repository_path": str(tmp_path),
                    "instruction": "Update documentation",
                    "policy_grant": "read_only",
                    "capabilities": [],
                },
            )
            execution_id = create_response.json()["execution_id"]
            
            # Cancel it
            cancel_response = await client.post(f"/executions/{execution_id}/cancel")
            assert cancel_response.status_code == 200
            data = cancel_response.json()
            assert data["execution_id"] == execution_id
            assert data["status"] in ["cancelled", "rejected", "failed", "succeeded"]
            
            # Verify the task status
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.status in [
                RuntimeTaskStatus.CANCELLED,
                RuntimeTaskStatus.REJECTED,
                RuntimeTaskStatus.FAILED,
                RuntimeTaskStatus.SUCCEEDED,
            ]


async def test_execution_not_found(tmp_path: Path) -> None:
    """Test that nonexistent execution_id returns 404."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)
    
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Try to get events for nonexistent execution
            events_response = await client.get("/executions/nonexistent-id/events")
            assert events_response.status_code == 404
            
            # Try to cancel nonexistent execution
            cancel_response = await client.post("/executions/nonexistent-id/cancel")
            assert cancel_response.status_code == 404


async def test_create_execution_with_no_connected_nodes(tmp_path: Path) -> None:
    """Test that execution creation works even when connected_nodes is 0."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)
    
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify no nodes connected
            ready_response = await client.get("/ready")
            assert ready_response.json()["connected_nodes"] == 0
            
            # Should still be able to create an execution
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_no_nodes",
                    "step_id": "step_no_nodes",
                    "repository_path": str(tmp_path),
                    "instruction": "Run in local mode",
                    "policy_grant": "read_only",
                    "capabilities": [],
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "execution_id" in data
            
            # Task should be created (may be queued or rejected, but should exist)
            execution_id = data["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.task_id == execution_id
