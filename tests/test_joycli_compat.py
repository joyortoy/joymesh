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


async def test_create_execution_with_dict_policy_grant(tmp_path: Path) -> None:
    """Test POST /executions accepts policy_grant as dict (JoyCLI format)."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test with policy_grant as dict with "profile" key
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_dict_policy",
                    "step_id": "step_dict",
                    "repository_path": str(tmp_path),
                    "instruction": "Test with dict policy grant",
                    "policy_grant": {"profile": "read_only", "other_metadata": "value"},
                    "capabilities": ["repository.read"],
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "execution_id" in data

            # Verify task was created with correct policy profile
            execution_id = data["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.task_id == execution_id
            assert task.policy_profile == "read_only"


async def test_create_execution_with_dict_policy_grant_mode_key(tmp_path: Path) -> None:
    """Test policy_grant dict with 'mode' key instead of 'profile'."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_mode_key",
                    "step_id": "step_mode",
                    "repository_path": str(tmp_path),
                    "instruction": "Test with mode key",
                    "policy_grant": {"mode": "read_only"},
                    "capabilities": [],
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "execution_id" in data

            execution_id = data["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.policy_profile == "read_only"


async def test_create_execution_with_dict_policy_grant_no_known_keys(tmp_path: Path) -> None:
    """Test policy_grant dict without recognized keys defaults to read_only."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "test_mission_unknown_keys",
                    "step_id": "step_unknown",
                    "repository_path": str(tmp_path),
                    "instruction": "Test with unknown keys",
                    "policy_grant": {"some_key": "some_value", "other": 123},
                    "capabilities": [],
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "execution_id" in data

            execution_id = data["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            # Should default to read_only when no recognized keys found
            assert task.policy_profile == "read_only"


async def test_execution_events_include_mission_and_step_ids(tmp_path: Path) -> None:
    """Test that events include execution_id, mission_id, and step_id as JoyCLI requires."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create execution with mission_id and step_id
            create_response = await client.post(
                "/executions",
                json={
                    "mission_id": "mission_abc123",
                    "step_id": "step_xyz789",
                    "repository_path": str(tmp_path),
                    "instruction": "Test mission and step tracking",
                    "policy_grant": "read_only",
                    "capabilities": ["repository.read"],
                },
            )
            assert create_response.status_code == 200
            execution_id = create_response.json()["execution_id"]

            # Get events
            events_response = await client.get(f"/executions/{execution_id}/events")
            assert events_response.status_code == 200
            data = events_response.json()
            assert "events" in data
            events = data["events"]

            # Every event MUST have execution_id, mission_id, step_id
            assert len(events) > 0, "Should have at least one event"
            for event in events:
                assert "execution_id" in event, f"Event missing execution_id: {event}"
                assert "mission_id" in event, f"Event missing mission_id: {event}"
                assert "step_id" in event, f"Event missing step_id: {event}"
                assert "event_type" in event, f"Event missing event_type: {event}"
                assert "payload" in event, f"Event missing payload: {event}"

                # Verify correct IDs
                assert event["execution_id"] == execution_id
                assert event["mission_id"] == "mission_abc123"
                assert event["step_id"] == "step_xyz789"

                # Verify event_type is valid for JoyCLI
                valid_types = [
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
                assert event["event_type"] in valid_types, (
                    f"Invalid event_type: {event['event_type']}"
                )


async def test_create_execution_default_remains_queue_only_without_route_opt_in(
    tmp_path: Path,
) -> None:
    """Without local_compat_route, /executions must not silently route."""
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "mission_queue_only",
                    "step_id": "step_queue_only",
                    "repository_path": str(tmp_path),
                    "instruction": "queue only probe",
                    "policy_grant": "read_only",
                    "capabilities": ["repository.read"],
                },
            )
            assert response.status_code == 200
            execution_id = response.json()["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.status is RuntimeTaskStatus.QUEUED
            assert task.preferred_connectors == ()
            assert task.selected_connector_id is None


async def test_create_execution_opt_in_routes_to_cursor_fixture(tmp_path: Path) -> None:
    """Explicit local_compat_route + cursor node fixture must select Cursor."""
    from joymesh.runtime_v1.service import build_ready_cursor_node

    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    workspace = str(tmp_path)
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        mesh.runtime_service.register_node(
            build_ready_cursor_node(
                node_id="mac-compat",
                workspace_id=workspace,
                local_path=workspace,
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "mission_route_cursor",
                    "step_id": "step_route_cursor",
                    "repository_path": workspace,
                    "instruction": "Summarise repository layout",
                    "policy_grant": "read_only",
                    "capabilities": [
                        "repository.read",
                        "repository.summarise",
                        "structured_output",
                    ],
                    "constraints": {
                        "local_compat_route": True,
                        "preferred_connectors": ["cursor"],
                    },
                },
            )
            assert response.status_code == 200, response.text
            execution_id = response.json()["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.preferred_connectors == ("cursor",)
            assert task.selected_connector_id == "cursor"
            assert task.status is RuntimeTaskStatus.LEASED
            # Queued-only must not be treated as success: leased is the routed start.
            assert task.status is not RuntimeTaskStatus.SUCCEEDED


async def test_create_execution_env_opt_in_defaults_preferred_connector_to_cursor(
    tmp_path: Path, monkeypatch
) -> None:
    """JOYMESH_JOYCLI_COMPAT_ROUTE alone must default preferred connector to cursor."""
    from unittest.mock import AsyncMock

    monkeypatch.setenv("JOYMESH_JOYCLI_COMPAT_ROUTE", "1")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        route = AsyncMock(side_effect=mesh.runtime_service.store.get_task)
        monkeypatch.setattr(mesh.runtime_service, "route_task", route)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "mission_env_route",
                    "step_id": "step_env_route",
                    "repository_path": str(tmp_path),
                    "instruction": "env opt-in probe",
                    "policy_grant": "read_only",
                    "capabilities": ["repository.read"],
                },
            )
            assert response.status_code == 200
            execution_id = response.json()["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.preferred_connectors == ("cursor",)
            route.assert_awaited_once_with(execution_id)
            # Without a node, routing may reject/queue — but skip_routing queue detail
            # must not be the path taken when env opt-in is set.
            assert task.detail != "Queued for routing when workers available"


async def test_create_execution_returns_immediately_with_no_nodes(tmp_path: Path) -> None:
    """Test that POST /executions returns quickly even when no nodes are connected."""
    import time

    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'joycli.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify no nodes connected
            ready_response = await client.get("/ready")
            assert ready_response.json()["connected_nodes"] == 0

            # Time the execution creation
            start_time = time.time()
            response = await client.post(
                "/executions",
                json={
                    "mission_id": "probe",
                    "step_id": "probe",
                    "repository_path": "/tmp",
                    "instruction": "probe only",
                    "policy_grant": {"mode": "read_only"},
                    "capabilities": [],
                },
            )
            elapsed_time = time.time() - start_time

            # Should return quickly (under 2 seconds)
            assert elapsed_time < 2.0, f"Request took {elapsed_time:.2f}s, expected < 2s"

            # Should return 200 with execution_id
            assert response.status_code == 200
            data = response.json()
            assert "execution_id" in data

            # Task should be queued (not rejected)
            execution_id = data["execution_id"]
            task = await mesh.runtime_service.store.get_task(execution_id)
            assert task.task_id == execution_id
            assert task.status in [
                RuntimeTaskStatus.QUEUED,
                RuntimeTaskStatus.ROUTING,
                RuntimeTaskStatus.PENDING,
                RuntimeTaskStatus.APPROVAL_REQUIRED,
            ]
