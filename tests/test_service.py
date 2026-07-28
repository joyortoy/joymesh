from pathlib import Path

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.models import EventType, RunStatus
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh


async def test_fake_run_is_persisted_and_normalized(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}")
    await mesh.initialize()
    try:
        run = await mesh.run(task="Exercise the fake harness", workspace=tmp_path)
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


async def test_active_fake_run_can_be_cancelled(tmp_path: Path) -> None:
    registry = AdapterRegistry([FakeHarnessAdapter(step_delay=1)])
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await mesh.initialize()
    try:
        run = await mesh.run(task="Wait for cancellation", workspace=tmp_path)
        for _ in range(100):
            current = await mesh.inspect_run(run.id)
            if current is not None and current.status is RunStatus.RUNNING:
                break
            import asyncio

            await asyncio.sleep(0.01)

        cancelled = await mesh.cancel(run.id)
        completed = await mesh.wait(run.id)

        assert cancelled.status is RunStatus.CANCELLED
        assert completed.status is RunStatus.CANCELLED
        assert (await mesh.events(run.id))[-1].type is EventType.RUN_CANCELLED
    finally:
        await mesh.close()
