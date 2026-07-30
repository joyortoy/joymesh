from pathlib import Path

import pytest

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import BillingRoute, SubscriptionCreate
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh
from tests.fixtures.fake_harness_definition import fake_harness_definition


@pytest.fixture
async def mesh(tmp_path: Path):
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    instance = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await instance.initialize()
    await instance.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="Bundled fake harness",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
            max_concurrency=8,
        )
    )
    yield instance
    await instance.close()


async def test_route_preview_is_deterministic(mesh: JoyMesh, tmp_path: Path) -> None:
    first = await mesh.preview_routes(task="demo", workspace=tmp_path)
    second = await mesh.preview_routes(task="demo", workspace=tmp_path)

    assert first == second
    assert first.selected is not None
    assert first.selected.harness_id == "fake"


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
    assert preview.selected.harness_id == "fake"
