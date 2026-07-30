from pathlib import Path

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import BillingRoute, EventType, RunStatus, SubscriptionCreate
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh
from tests.fixtures.fake_harness_definition import fake_harness_definition


def _fake_mesh(tmp_path: Path, *, step_delay: float = 0.01) -> JoyMesh:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter(step_delay=step_delay)],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    return JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )


async def test_fake_run_is_persisted_and_normalized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    from joymesh.config import HarnessPreferences, save_harness_preferences

    save_harness_preferences(HarnessPreferences(enabled=("fake",), default="fake"))
    mesh = _fake_mesh(tmp_path)
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
    try:
        run = await mesh.run(
            task="Exercise the fake harness", workspace=tmp_path, harness="fake"
        )
        completed = await mesh.wait(run.id)
        events = await mesh.events(run.id)

        assert completed.status is RunStatus.COMPLETED
        assert completed.exit_code == 0
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert events[0].type is EventType.RUN_QUEUED
        assert events[-1].type is EventType.RUN_COMPLETED
        assert any(event.type is EventType.HARNESS_PROGRESS for event in events)
    finally:
        await mesh.close()


async def test_active_fake_run_can_be_cancelled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    import asyncio

    from joymesh.config import HarnessPreferences, save_harness_preferences

    save_harness_preferences(HarnessPreferences(enabled=("fake",), default="fake"))
    mesh = _fake_mesh(tmp_path, step_delay=1)
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
    try:
        run = await mesh.run(task="Wait for cancellation", workspace=tmp_path, harness="fake")
        for _ in range(100):
            current = await mesh.inspect_run(run.id)
            if current is not None and current.status is RunStatus.RUNNING:
                break
            await asyncio.sleep(0.01)

        cancelled = await mesh.cancel(run.id)
        completed = await mesh.wait(run.id)

        assert cancelled.status is RunStatus.CANCELLED
        assert completed.status is RunStatus.CANCELLED
        assert (await mesh.events(run.id))[-1].type is EventType.RUN_CANCELLED
    finally:
        await mesh.close()
