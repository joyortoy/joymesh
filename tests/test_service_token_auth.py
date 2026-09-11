from pathlib import Path

from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.service import JoyMesh


def _runtime_body() -> dict[str, object]:
    return {
        "workspace_id": "workspace",
        "prompt": "Summarise the repository",
        "requested_capabilities": ["repository.read"],
    }


async def test_service_token_is_optional_when_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JOYMESH_TOKEN", raising=False)
    monkeypatch.delenv("JOYMESH_SERVICE_TOKEN", raising=False)
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'open.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/runtime/tasks", json=_runtime_body())

    assert response.status_code == 200


async def test_service_token_protects_mutations_but_not_health(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOYMESH_TOKEN", "service-secret")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'protected.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            health = await client.get("/api/v1/health")
            missing = await client.post("/runtime/tasks", json=_runtime_body())
            missing_run = await client.post(
                "/api/v1/runs",
                json={"task": "Summarise", "workspace": str(tmp_path)},
            )
            missing_cancel = await client.post("/runtime/tasks/unknown/cancel")
            missing_retry = await client.post("/runtime/tasks/unknown/retry")
            invalid = await client.post(
                "/runtime/tasks",
                json=_runtime_body(),
                headers={"Authorization": "Bearer wrong"},
            )
            valid = await client.post(
                "/runtime/tasks",
                json=_runtime_body(),
                headers={"Authorization": "Bearer service-secret"},
            )

    assert health.status_code == 200
    assert missing.status_code == 401
    assert missing_run.status_code == 401
    assert missing_cancel.status_code == 401
    assert missing_retry.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200


async def test_service_token_alias_is_accepted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JOYMESH_TOKEN", raising=False)
    monkeypatch.setenv("JOYMESH_SERVICE_TOKEN", "alias-secret")
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'alias.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/runtime/tasks",
                json=_runtime_body(),
                headers={"Authorization": "Bearer alias-secret"},
            )

    assert response.status_code == 200
