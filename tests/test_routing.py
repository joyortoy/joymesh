from pathlib import Path

import pytest

from joymesh.models import BillingRoute, SubscriptionCreate
from joymesh.service import JoyMesh


@pytest.fixture
async def mesh(tmp_path: Path):
    instance = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}")
    await instance.initialize()
    yield instance
    await instance.close()


async def test_route_preview_is_deterministic(mesh: JoyMesh, tmp_path: Path) -> None:
    first = await mesh.preview_routes(task="demo", workspace=tmp_path)
    second = await mesh.preview_routes(task="demo", workspace=tmp_path)

    assert first == second
    assert first.selected is not None
    assert first.selected.harness_id == "fake"
    assert first.selected.subscription_id == "fake-local"


async def test_route_excludes_exhausted_subscription(mesh: JoyMesh, tmp_path: Path) -> None:
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="Exhausted API route",
            billing_route=BillingRoute.API,
            monthly_limit=10,
            used_amount=10,
            quota_known=True,
            cost_weight=0,
        )
    )

    preview = await mesh.preview_routes(task="demo", workspace=tmp_path)

    exhausted = [
        candidate
        for candidate in preview.candidates
        if "configured quota reserve reached" in candidate.reasons
    ]
    assert len(exhausted) == 1
    assert not exhausted[0].eligible
    assert preview.selected is not None
    assert preview.selected.subscription_id == "fake-local"
