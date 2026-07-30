from pathlib import Path

from httpx import ASGITransport, AsyncClient

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.api import create_app
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import BillingRoute, SubscriptionCreate
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh
from tests.fixtures.fake_harness_definition import fake_harness_definition


async def test_api_production_harnesses_exclude_fake(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api-prod.db'}")
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            harnesses = await client.get("/api/v1/harnesses")
            assert harnesses.status_code == 200
            ids = {item["manifest"]["harness_id"] for item in harnesses.json()}
            assert "fake" not in ids
            assert "joy" not in ids
            catalogue = await client.get("/api/v1/harnesses/catalogue")
            assert catalogue.status_code == 200
            catalogue_ids = {item["id"] for item in catalogue.json()}
            assert "fake" not in catalogue_ids
            assert "joy" not in catalogue_ids


async def test_api_vertical_slice_and_sse(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    from joymesh.config import HarnessPreferences, save_harness_preferences

    save_harness_preferences(HarnessPreferences(enabled=("fake",), default="fake"))
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        registry=registry,
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="test",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    app = create_app(mesh)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            harnesses = await client.get("/api/v1/harnesses")
            assert harnesses.status_code == 200
            assert any(item["manifest"]["harness_id"] == "fake" for item in harnesses.json())

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
            body = stream.text
            assert "run.queued" in body or "RUN_QUEUED" in body or "event:" in body
