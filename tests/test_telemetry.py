"""Tests for opt-in anonymous execution metrics consent and reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from joymesh.cli import app
from joymesh.config import (
    MetricsMode,
    MetricsSettings,
    TelemetryMode,
    UserConfig,
    load_user_config,
    save_user_config,
    set_metrics_mode,
    set_telemetry_mode,
    user_config_from_mapping,
)
from joymesh.models import Run, RunStatus
from joymesh.telemetry import (
    METRICS_PAYLOAD_ALLOWLIST,
    AnonymousExecutionMetrics,
    AnonymousExecutionReport,
    RecordingTelemetryTransport,
    TelemetryService,
    build_metrics_from_run,
    consent_needed,
    ensure_consent,
    model_family,
    preview_metrics_placeholder,
    prompt_consent,
    render_consent_dialog,
    render_preview_yaml,
    reset_telemetry_service_for_tests,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _telemetry_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("JOYMESH_TELEMETRY_ENDPOINT", raising=False)
    reset_telemetry_service_for_tests()
    yield
    reset_telemetry_service_for_tests()


def test_config_roundtrip_yaml_metrics_key(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    set_metrics_mode(MetricsMode.ASK, path=path)
    loaded = load_user_config(path)
    assert loaded.metrics.mode is MetricsMode.ASK
    assert loaded.metrics.consent_completed is True
    text = path.read_text(encoding="utf-8")
    assert "metrics:" in text
    assert "mode: ask" in text


def test_config_reads_legacy_telemetry_key(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("telemetry:\n  mode: never\n  consent_completed: true\n", encoding="utf-8")
    loaded = load_user_config(path)
    assert loaded.metrics.mode is MetricsMode.NEVER
    assert loaded.metrics.consent_completed is True


def test_consent_dialog_has_no_preselected_option() -> None:
    text = render_consent_dialog()
    assert "Help improve JoyMesh?" in text
    assert "What is NEVER sent" in text
    assert "Prompts" in text
    assert "( ) 1) Always send anonymous execution metrics" in text
    assert "( ) 2) Ask every time" in text
    assert "( ) 3) Never send" in text
    assert "●" not in text
    assert "None is selected" in text


def test_consent_requires_explicit_choice() -> None:
    messages: list[str] = []
    answers = iter(["", "not-a-choice", "1"])

    mode = prompt_consent(input_fn=lambda _p: next(answers), output_fn=messages.append)
    assert mode is MetricsMode.ALWAYS
    assert any("No option selected" in message for message in messages)


def test_ensure_consent_persists_only_after_choice(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    before = load_user_config(path)
    assert before.metrics.consent_completed is False
    answers = iter(["3"])
    config = ensure_consent(
        interactive=True,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _t: None,
        config_path=path,
    )
    assert config.metrics.mode is MetricsMode.NEVER
    assert config.metrics.consent_completed is True


def test_existing_users_are_not_prompted_again(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    save_user_config(
        UserConfig(metrics=MetricsSettings(mode=MetricsMode.ASK, consent_completed=True)),
        path=path,
    )
    calls = {"n": 0}

    def _input(_prompt: str) -> str:
        calls["n"] += 1
        raise AssertionError("existing users must not be prompted")

    config = ensure_consent(
        interactive=True,
        input_fn=_input,
        output_fn=lambda _t: None,
        config_path=path,
    )
    assert calls["n"] == 0
    assert config.metrics.mode is MetricsMode.ASK
    assert consent_needed(config) is False


def test_no_send_until_consent_completed() -> None:
    transport = RecordingTelemetryTransport()
    service = TelemetryService(transport=transport)
    report = AnonymousExecutionMetrics(task_type="bug_fix", quality="good")
    assert service.maybe_send(report, interactive=False) is None
    assert transport.reports == []


def test_always_mode_sends_non_blocking() -> None:
    transport = RecordingTelemetryTransport()
    service = TelemetryService(transport=transport)
    service.set_mode(MetricsMode.ALWAYS)
    report = AnonymousExecutionMetrics(
        task_type="bug_fix",
        duration_ms=100,
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        quality="good",
    )
    future = service.maybe_send(report, interactive=False)
    assert future is not None
    future.result(timeout=2)
    assert len(transport.reports) == 1
    payload = transport.reports[0].as_dict()
    assert set(payload) <= METRICS_PAYLOAD_ALLOWLIST
    assert payload["task_type"] == "bug_fix"
    assert "prompt" not in payload


def test_never_mode_does_not_send() -> None:
    transport = RecordingTelemetryTransport()
    service = TelemetryService(transport=transport)
    service.set_mode(MetricsMode.NEVER)
    future = service.maybe_send(AnonymousExecutionMetrics(quality="good"), interactive=False)
    assert future is None
    assert transport.reports == []


def test_ask_mode_respects_decline() -> None:
    transport = RecordingTelemetryTransport()
    service = TelemetryService(transport=transport)
    service.set_mode(MetricsMode.ASK)
    answers = iter(["n"])
    future = service.maybe_send(
        AnonymousExecutionMetrics(quality="good"),
        interactive=True,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _t: None,
    )
    assert future is None
    assert transport.reports == []


def test_ask_mode_sends_on_accept() -> None:
    transport = RecordingTelemetryTransport()
    service = TelemetryService(transport=transport)
    service.set_mode(MetricsMode.ASK)
    answers = iter(["y"])
    future = service.maybe_send(
        AnonymousExecutionMetrics(quality="good"),
        interactive=True,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _t: None,
    )
    assert future is not None
    future.result(timeout=2)
    assert len(transport.reports) == 1


def test_transport_failure_does_not_raise() -> None:
    class Boom:
        def send(self, report: AnonymousExecutionMetrics) -> None:
            del report
            raise RuntimeError("network down")

    service = TelemetryService(transport=Boom())  # type: ignore[arg-type]
    service.set_mode(MetricsMode.ALWAYS)
    future = service.maybe_send(AnonymousExecutionMetrics(quality="good"), interactive=False)
    assert future is not None
    future.result(timeout=2)


def test_metrics_payload_allowlist_and_sensitive_exclusion() -> None:
    metrics = AnonymousExecutionMetrics(
        task_type="bug_fix",
        duration_ms=50,
        usage={
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
            "secret_dump": 99,  # type: ignore[dict-item]
        },
        quality="good",
    )
    payload = metrics.as_dict()
    assert set(payload) <= METRICS_PAYLOAD_ALLOWLIST
    assert payload["usage"] == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }
    dumped = json.dumps(payload)
    assert "secret" not in dumped
    assert "prompt" not in dumped


def test_build_metrics_from_run_excludes_sensitive_fields() -> None:
    now = datetime.now(UTC)
    run = Run(
        id="run_1",
        task="SECRET PROMPT with /Users/me/private",
        workspace="/Users/me/secret-repo",
        harness_id="codex",
        status=RunStatus.COMPLETED,
        created_at=now,
        started_at=now,
        finished_at=now + timedelta(seconds=2),
        exit_code=0,
        error="traceback /Users/me/app.py",
        task_context_id="ctx",
    )
    metrics = build_metrics_from_run(
        run,
        usage={"input_tokens": 9, "output_tokens": 2},
        task_type="bug_fix",
    )
    payload = metrics.as_dict()
    dumped = json.dumps(payload)
    assert "SECRET" not in dumped
    assert "/Users" not in dumped
    assert "traceback" not in dumped
    assert payload["task_type"] == "bug_fix"
    assert payload["quality"] == "good"
    assert payload["usage"]["total_tokens"] == 11
    assert set(payload) <= METRICS_PAYLOAD_ALLOWLIST


def test_legacy_report_converts_to_metrics() -> None:
    report = AnonymousExecutionReport(
        task_category="refactor",
        duration_ms=10,
        tokens={"input": 1, "output": 2},
        success=True,
    )
    metrics = report.to_metrics()
    assert metrics.as_dict()["task_type"] == "refactor"
    assert metrics.as_dict()["quality"] == "good"


def test_cli_metrics_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    status = runner.invoke(app, ["metrics", "status"])
    assert status.exit_code == 0
    assert "mode: unset" in status.output

    assert runner.invoke(app, ["metrics", "on"]).exit_code == 0
    assert "mode: always" in runner.invoke(app, ["metrics", "status"]).output
    assert runner.invoke(app, ["metrics", "ask"]).exit_code == 0
    assert "mode: ask" in runner.invoke(app, ["metrics", "status"]).output
    assert runner.invoke(app, ["metrics", "off"]).exit_code == 0
    assert "mode: never" in runner.invoke(app, ["metrics", "status"]).output


def test_cli_metrics_preview_uses_placeholders_only() -> None:
    result = runner.invoke(app, ["metrics", "preview"])
    assert result.exit_code == 0, result.output
    assert "placeholders only" in result.output.lower()
    assert "task_type: code_edit" in result.output
    assert "quality: good" in result.output
    assert "/Users" not in result.output
    assert set(preview_metrics_placeholder()) <= METRICS_PAYLOAD_ALLOWLIST
    assert "duration_ms:" in render_preview_yaml()


def test_cli_init_records_consent() -> None:
    result = runner.invoke(app, ["init"], input="2\n")
    assert result.exit_code == 0, result.output
    config = load_user_config()
    assert config.metrics.mode is MetricsMode.ASK
    assert config.metrics.consent_completed is True


def test_user_config_from_mapping_defaults() -> None:
    config = user_config_from_mapping({"metrics": {"mode": "always"}})
    assert config.metrics.mode is MetricsMode.ALWAYS
    assert config.telemetry.mode is TelemetryMode.ALWAYS
    set_telemetry_mode(TelemetryMode.NEVER)
    assert load_user_config().metrics.mode is MetricsMode.NEVER


def test_model_family_strips_provider_paths() -> None:
    assert model_family("accounts/org/models/claude-sonnet") == "claude-sonnet"
    assert model_family(None) is None
