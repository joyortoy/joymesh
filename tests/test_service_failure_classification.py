"""Service-layer safe exception classification for harness execution failures."""

from __future__ import annotations

from pathlib import Path

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import BillingRoute, EventType, RunStatus, SubscriptionCreate
from joymesh.registry import AdapterRegistry
from joymesh.runtime import HarnessStreamOverflowError
from joymesh.service import (
    JoyMesh,
    classify_execution_exception,
    format_safe_failure_error,
)
from tests.fixtures.fake_harness_definition import fake_harness_definition


def _mesh(tmp_path: Path) -> JoyMesh:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter(step_delay=0.01)],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    return JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )


def test_classify_nested_stream_overflow_is_diagnosable() -> None:
    leaf = HarnessStreamOverflowError("stream record exceeded 4194304 bytes without a newline")
    group = ExceptionGroup("unhandled errors in a TaskGroup", (leaf,))
    classification = classify_execution_exception(group)
    assert classification["exception_type"] == "ExceptionGroup"
    assert classification["leaves"] == [
        {"type": "HarnessStreamOverflowError", "reason": "stream_record_overflow"}
    ]
    text = format_safe_failure_error(classification)
    assert "HarnessStreamOverflowError" in text
    assert "stream_record_overflow" in text
    assert "4194304" not in text
    assert "TaskGroup" not in text or "ExceptionGroup" in text


def test_classify_sensitive_callback_exception_does_not_leak() -> None:
    secret = "sk-live-super-secret-token"
    argv = "['/usr/bin/codex', '--api-key', 'sk-live-super-secret-token', 'huge-' + 'x' * 9000]"
    leaf = RuntimeError(f"callback blew up token={secret} argv={argv}")
    group = ExceptionGroup("unhandled errors in a TaskGroup", (leaf,))
    classification = classify_execution_exception(group)
    blob = format_safe_failure_error(classification) + str(classification)
    assert secret not in blob
    assert "codex" not in blob
    assert "argv" not in blob
    assert "huge-" not in blob
    assert classification["leaves"] == [{"type": "RuntimeError", "reason": "unexpected_error"}]


async def test_execute_persists_nested_overflow_without_taskgroup_only_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    from joymesh.config import HarnessPreferences, save_harness_preferences

    save_harness_preferences(HarnessPreferences(enabled=("fake",), default="fake"))
    mesh = _mesh(tmp_path)
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

    async def boom(**_kwargs):
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            (HarnessStreamOverflowError("stream record exceeded 4194304 bytes without a newline"),),
        )

    monkeypatch.setattr(mesh.runtime, "execute", boom)
    try:
        run = await mesh.run(task="overflow", workspace=tmp_path, harness="fake")
        completed = await mesh.wait(run.id)
        assert completed.status is RunStatus.FAILED
        assert completed.error is not None
        assert "HarnessStreamOverflowError" in completed.error
        assert "stream_record_overflow" in completed.error
        assert "unhandled errors in a TaskGroup" not in completed.error
        events = await mesh.events(run.id)
        failed = [e for e in events if e.type is EventType.RUN_FAILED]
        assert failed
        assert failed[-1].payload.get("leaves")
        assert failed[-1].payload["leaves"][0]["type"] == "HarnessStreamOverflowError"
    finally:
        await mesh.close()


async def test_execute_persists_callback_failure_without_secret_leak(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    from joymesh.config import HarnessPreferences, save_harness_preferences

    save_harness_preferences(HarnessPreferences(enabled=("fake",), default="fake"))
    mesh = _mesh(tmp_path)
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
    secret = "PASSWORD=hunter2-do-not-leak"
    huge = "RECORD=" + ("Z" * 5000)

    async def boom(**_kwargs):
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            (RuntimeError(f"on_line failed {secret} {huge} argv=['secret-bin']"),),
        )

    monkeypatch.setattr(mesh.runtime, "execute", boom)
    try:
        run = await mesh.run(task="callback-fail", workspace=tmp_path, harness="fake")
        completed = await mesh.wait(run.id)
        assert completed.status is RunStatus.FAILED
        assert completed.error == "ExceptionGroup:[RuntimeError:unexpected_error]"
        events = await mesh.events(run.id)
        failed = next(e for e in events if e.type is EventType.RUN_FAILED)
        combined = completed.error + failed.message + str(failed.payload)
        assert "hunter2" not in combined
        assert "PASSWORD" not in combined
        assert "RECORD=" not in combined
        assert "secret-bin" not in combined
        assert "argv" not in combined
    finally:
        await mesh.close()
