from pathlib import Path

from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.service import JoyMesh


async def test_api_vertical_slice_and_sse(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            harnesses = await client.get("/api/v1/harnesses")
            assert harnesses.status_code == 200
            assert harnesses.json()[0]["manifest"]["harness_id"] == "fake"

            preview = await client.post(
                "/api/v1/routes/preview",
                json={"task": "API demo", "workspace": str(tmp_path)},
            )
            assert preview.status_code == 200
            route = preview.json()["selected"]

            created = await client.post(
                "/api/v1/runs",
                json={
                    "task": "API demo",
                    "workspace": str(tmp_path),
                    "route": route,
                },
            )
            assert created.status_code == 202
            run_id = created.json()["id"]

            stream = await client.get(f"/api/v1/runs/{run_id}/events")
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert "event: run.succeeded" in stream.text

            inspected = await client.get(f"/api/v1/runs/{run_id}")
            assert inspected.json()["status"] == "succeeded"

            dashboard = await client.get("/")
            assert dashboard.status_code == 200
            assert "JoyMesh Console" in dashboard.text

    await mesh.close()
