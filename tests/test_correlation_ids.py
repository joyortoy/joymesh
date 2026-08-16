from joymesh.models import RunRequest
from joymesh.runtime_v1.execution_routing.bridge import mission_spec_from_task
from joymesh.runtime_v1.models import CreateRuntimeTaskBody
from joymesh.runtime_v1.service import RuntimeService


async def test_runtime_correlation_fields_pass_through_to_mission() -> None:
    service = RuntimeService()
    task = await service.create_task(
        CreateRuntimeTaskBody(
            workspace_id="workspace",
            prompt="Summarise the repository",
            requested_capabilities=("repository.read",),
            correlation_id="correlation-123",
            mission_id="mission-123",
            execution_id="execution-123",
            trace_id="trace-123",
            actor_id="actor-123",
            idempotency_key="idempotency-123",
        ),
        user_id="integration",
    )
    stored = await service.store.get_task(task.task_id)
    mission = mission_spec_from_task(
        stored,
        prompt="Summarise the repository",
        workspace_path="/tmp/workspace",
    )

    assert stored.correlation_id == "correlation-123"
    assert stored.metadata["execution_id"] == "execution-123"
    assert stored.metadata["trace_id"] == "trace-123"
    assert mission.correlation_id == "correlation-123"
    assert mission.mission_id == "mission-123"
    assert mission.metadata["runtime_task_id"] == stored.task_id
    assert mission.metadata["actor_id"] == "actor-123"
    assert mission.metadata["idempotency_key"] == "idempotency-123"


async def test_runtime_correlation_defaults_to_task_id() -> None:
    service = RuntimeService()
    task = await service.create_task(
        CreateRuntimeTaskBody(
            workspace_id="workspace",
            prompt="Summarise the repository",
            requested_capabilities=("repository.read",),
        ),
        user_id="integration",
    )

    assert task.correlation_id == task.task_id


def test_run_request_accepts_correlation_fields() -> None:
    request = RunRequest(
        task="Summarise",
        workspace=".",
        correlation_id="correlation-123",
        mission_id="mission-123",
        trace_id="trace-123",
        execution_id="execution-123",
    )

    assert request.correlation_id == "correlation-123"
    assert request.mission_id == "mission-123"
    assert request.trace_id == "trace-123"
    assert request.execution_id == "execution-123"
