"""Anonymous execution metrics — consent, allowlisted payloads, best-effort transport.

Separate from the execution runtime. Metrics are opt-in and never transmitted
before explicit user consent. Payload serialization uses an explicit allowlist.
"""

from __future__ import annotations

import platform
import sys
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from joymesh.config import (
    MetricsMode,
    MetricsSettings,
    TelemetryMode,
    TelemetrySettings,
    UserConfig,
    default_config_path,
    load_user_config,
    save_user_config,
    set_metrics_mode,
    set_telemetry_mode,
)

# Extensible schema version for internal tracking; not part of JoyCLI metrics allowlist.
REPORT_SCHEMA_VERSION = 1

# JoyCLI anonymous execution metrics — only these top-level keys may be transmitted.
METRICS_PAYLOAD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "task_type",
        "duration_ms",
        "usage",
        "quality",
    }
)
USAGE_ALLOWLIST: frozenset[str] = frozenset({"input_tokens", "output_tokens", "total_tokens"})
QUALITY_ALLOWLIST: frozenset[str] = frozenset({"good", "bad", "unknown"})

# Legacy telemetry allowlist retained for AnonymousExecutionReport compatibility.
PAYLOAD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "task_category",
        "harness",
        "connector",
        "model_family",
        "duration_ms",
        "tokens",
        "success",
        "retry_count",
        "error_category",
        "joymesh_version",
        "platform",
        "schema_version",
    }
)
TOKENS_ALLOWLIST: frozenset[str] = frozenset({"input", "output"})

CONSENT_TITLE = "Help improve JoyMesh?"

CONSENT_BODY = """\
JoyMesh can optionally send anonymous execution statistics to JoyCLI.

These reports help improve routing, performance, and future model evaluation.

What is sent:
  ✓ Task type
  ✓ Duration
  ✓ Token usage
  ✓ Quality (good / bad / unknown)

What is NEVER sent:
  ✗ Prompts
  ✗ AI responses
  ✗ Source code
  ✗ Files
  ✗ Repository names
  ✗ Credentials
  ✗ Personal information
"""

_OPTION_ALWAYS = "always"
_OPTION_ASK = "ask"
_OPTION_NEVER = "never"

_CHOICE_LABELS = {
    "1": _OPTION_ALWAYS,
    "2": _OPTION_ASK,
    "3": _OPTION_NEVER,
    "always": _OPTION_ALWAYS,
    "ask": _OPTION_ASK,
    "never": _OPTION_NEVER,
}


@dataclass(frozen=True)
class AnonymousExecutionMetrics:
    """Allowlisted anonymous execution metrics for JoyCLI — safe to transmit."""

    task_type: str | None = None
    duration_ms: int | None = None
    usage: dict[str, int] | None = None
    quality: str | None = None

    def as_dict(self) -> dict[str, Any]:
        usage: dict[str, int] | None = None
        if self.usage:
            usage = {key: int(value) for key, value in self.usage.items() if key in USAGE_ALLOWLIST}
            if not usage:
                usage = None
        quality = self.quality if self.quality in QUALITY_ALLOWLIST else "unknown"
        candidates: dict[str, Any] = {
            "task_type": self.task_type,
            "duration_ms": self.duration_ms,
            "usage": usage,
            "quality": quality,
        }
        return {key: candidates[key] for key in METRICS_PAYLOAD_ALLOWLIST if key in candidates}


@dataclass(frozen=True)
class AnonymousExecutionReport:
    """Legacy anonymous report shape — converted to metrics before transmission."""

    task_category: str | None = None
    harness: str | None = None
    connector: str | None = None
    model_family: str | None = None
    duration_ms: int | None = None
    tokens: dict[str, int] | None = None
    success: bool | None = None
    retry_count: int = 0
    error_category: str | None = None
    joymesh_version: str = field(default_factory=lambda: joymesh_version())
    platform: str = field(default_factory=lambda: platform_label())
    schema_version: int = REPORT_SCHEMA_VERSION
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialize legacy fields with an explicit allowlist (not for JoyCLI send)."""

        tokens: dict[str, int] | None = None
        if self.tokens:
            tokens = {
                key: int(value) for key, value in self.tokens.items() if key in TOKENS_ALLOWLIST
            }
            if not tokens:
                tokens = None
        candidates: dict[str, Any] = {
            "task_category": self.task_category,
            "harness": self.harness,
            "connector": self.connector,
            "model_family": self.model_family,
            "duration_ms": self.duration_ms,
            "tokens": tokens,
            "success": self.success,
            "retry_count": self.retry_count,
            "error_category": self.error_category,
            "joymesh_version": self.joymesh_version,
            "platform": self.platform,
            "schema_version": self.schema_version,
        }
        for key, value in self.extras.items():
            if key in PAYLOAD_ALLOWLIST and key not in candidates:
                candidates[key] = value
        return {key: candidates[key] for key in PAYLOAD_ALLOWLIST if key in candidates}

    def to_metrics(self) -> AnonymousExecutionMetrics:
        usage = None
        if self.tokens:
            input_tokens = int(self.tokens.get("input") or 0)
            output_tokens = int(self.tokens.get("output") or 0)
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
        if self.success is True:
            quality = "good"
        elif self.success is False:
            quality = "bad"
        else:
            quality = "unknown"
        return AnonymousExecutionMetrics(
            task_type=self.task_category,
            duration_ms=self.duration_ms,
            usage=usage,
            quality=quality,
        )


def preview_metrics_placeholder() -> dict[str, Any]:
    """Representative placeholder metrics — never real execution data."""

    return {
        "task_type": "code_edit",
        "duration_ms": 48210,
        "usage": {
            "input_tokens": 12450,
            "output_tokens": 2380,
            "total_tokens": 14830,
        },
        "quality": "good",
    }


def preview_report_placeholder() -> dict[str, Any]:
    """Legacy placeholder; prefer preview_metrics_placeholder for JoyCLI metrics."""

    return preview_metrics_placeholder()


def render_preview_yaml(payload: dict[str, Any] | None = None) -> str:
    data = payload if payload is not None else preview_metrics_placeholder()
    allowlist = (
        METRICS_PAYLOAD_ALLOWLIST
        if set(data.keys()) <= METRICS_PAYLOAD_ALLOWLIST | {"usage"} or "task_type" in data
        else PAYLOAD_ALLOWLIST
    )
    lines: list[str] = []
    for key in ("task_type", "duration_ms", "usage", "quality"):
        if key not in data or key not in allowlist:
            if key not in data:
                continue
        value = data[key]
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for nested_key in ("input_tokens", "output_tokens", "total_tokens"):
                if nested_key in value:
                    lines.append(f"  {nested_key}: {value[nested_key]}")
            continue
        if value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) + "\n"


def joymesh_version() -> str:
    try:
        return version("joymesh")
    except PackageNotFoundError:
        return "0.1.0"


def platform_label() -> str:
    system = platform.system()
    mapping = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}
    return mapping.get(system, system or "unknown")


def model_family(model: str | None) -> str | None:
    """Reduce a model id to a coarse family label (no paths or accounts)."""

    if not model:
        return None
    text = str(model).strip()
    if not text:
        return None
    # Drop account/org path segments commonly seen in provider ids.
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if ":" in text:
        text = text.split(":", 1)[0]
    # Keep only a short alphanumeric/dash/dot family token.
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in ".-_")
    if not cleaned:
        return None
    return cleaned[:64]


def classify_error_category(*, status: str | None, exit_code: int | None) -> str | None:
    if status in {"completed", "succeeded", "success"}:
        return None
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"timed_out", "timeout"}:
        return "timeout"
    if status in {"failed", "error"}:
        if exit_code == 127:
            return "executable_missing"
        if exit_code is not None and exit_code != 0:
            return "process_failure"
        return "execution_failure"
    if exit_code is not None and exit_code != 0:
        return "process_failure"
    return None


def duration_ms_from_times(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    delta = finished_at - started_at
    return max(0, int(delta.total_seconds() * 1000))


def build_report_from_run(
    run: Any,
    *,
    usage: Mapping[str, Any] | None = None,
    task_category: str | None = None,
    connector: str | None = None,
    model: str | None = None,
    retry_count: int = 0,
    extras: Mapping[str, Any] | None = None,
) -> AnonymousExecutionReport:
    """Build a report from a Run-like object without reading prompts or paths."""

    status = getattr(run, "status", None)
    if status is None:
        status_value = ""
    elif hasattr(status, "value"):
        status_value = str(status.value)
    else:
        status_value = str(status)
    exit_code = getattr(run, "exit_code", None)
    success = status_value in {"completed", "succeeded", "success"} and (
        exit_code is None or exit_code == 0
    )
    tokens = None
    if usage:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is not None or output_tokens is not None:
            tokens = {
                "input": int(input_tokens or 0),
                "output": int(output_tokens or 0),
            }
    return AnonymousExecutionReport(
        task_category=task_category,
        harness=getattr(run, "harness_id", None),
        connector=connector,
        model_family=model_family(model),
        duration_ms=duration_ms_from_times(
            getattr(run, "started_at", None), getattr(run, "finished_at", None)
        ),
        tokens=tokens,
        success=success,
        retry_count=max(0, int(retry_count)),
        error_category=classify_error_category(status=status_value, exit_code=exit_code),
        extras=dict(extras or {}),
    )


def build_metrics_from_run(
    run: Any,
    *,
    usage: Mapping[str, Any] | None = None,
    task_type: str | None = None,
) -> AnonymousExecutionMetrics:
    """Build JoyCLI metrics from a Run without reading prompts or paths."""

    status = getattr(run, "status", None)
    if status is None:
        status_value = ""
    elif hasattr(status, "value"):
        status_value = str(status.value)
    else:
        status_value = str(status)
    exit_code = getattr(run, "exit_code", None)
    success = status_value in {"completed", "succeeded", "success"} and (
        exit_code is None or exit_code == 0
    )
    if success:
        quality = "good"
    elif status_value in {"failed", "error", "timed_out", "timeout"} or (
        exit_code is not None and exit_code != 0
    ):
        quality = "bad"
    else:
        quality = "unknown"

    usage_payload = None
    if usage:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        usage_payload = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    return AnonymousExecutionMetrics(
        task_type=task_type,
        duration_ms=duration_ms_from_times(
            getattr(run, "started_at", None), getattr(run, "finished_at", None)
        ),
        usage=usage_payload,
        quality=quality,
    )


def build_report_from_execution(
    *,
    task_category: str | None,
    harness: str | None,
    connector: str | None,
    model: str | None,
    duration_ms: int | None,
    tokens: Mapping[str, int] | None,
    success: bool,
    retry_count: int = 0,
    error_category: str | None = None,
    extras: Mapping[str, Any] | None = None,
) -> AnonymousExecutionReport:
    token_payload = None
    if tokens is not None:
        token_payload = {
            "input": int(tokens.get("input") or tokens.get("input_tokens") or 0),
            "output": int(tokens.get("output") or tokens.get("output_tokens") or 0),
        }
    return AnonymousExecutionReport(
        task_category=task_category,
        harness=harness,
        connector=connector,
        model_family=model_family(model),
        duration_ms=duration_ms,
        tokens=token_payload,
        success=success,
        retry_count=max(0, int(retry_count)),
        error_category=error_category,
        extras=dict(extras or {}),
    )


class TelemetryTransport(Protocol):
    def send(self, report: AnonymousExecutionMetrics) -> None: ...


class NullTelemetryTransport:
    def send(self, report: AnonymousExecutionMetrics) -> None:
        del report


@dataclass
class RecordingTelemetryTransport:
    reports: list[AnonymousExecutionMetrics] = field(default_factory=list)

    def send(self, report: AnonymousExecutionMetrics) -> None:
        self.reports.append(report)


class HttpTelemetryTransport:
    """POST JSON metrics to an HTTPS endpoint. Failures are swallowed by the service."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 5.0) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def send(self, report: AnonymousExecutionMetrics) -> None:
        import json

        body = json.dumps(report.as_dict(), separators=(",", ":")).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"joymesh-metrics/{joymesh_version()}",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            response.read()


def default_telemetry_endpoint() -> str | None:
    import os

    value = os.environ.get("JOYMESH_TELEMETRY_ENDPOINT", "").strip()
    return value or None


def default_transport() -> TelemetryTransport:
    endpoint = default_telemetry_endpoint()
    if endpoint:
        return HttpTelemetryTransport(endpoint)
    return NullTelemetryTransport()


def render_consent_dialog() -> str:
    return (
        f"{CONSENT_TITLE}\n\n"
        f"{CONSENT_BODY}\n"
        "Please choose one. None is selected until you choose:\n\n"
        "  ( ) 1) Always send anonymous execution metrics\n"
        "  ( ) 2) Ask every time\n"
        "  ( ) 3) Never send\n"
    )


def prompt_consent(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] | None = None,
) -> MetricsMode:
    """Interactive consent. Requires an explicit choice — Enter alone is not consent."""

    write = output_fn or (lambda text: print(text, file=sys.stderr))
    write(render_consent_dialog())
    while True:
        raw = input_fn("Enter 1, 2, or 3 to continue: ").strip().lower()
        if not raw:
            write("No option selected. Choose 1, 2, or 3 before continuing.")
            continue
        mapped = _CHOICE_LABELS.get(raw)
        if mapped is None:
            write("Please choose 1, 2, or 3. No option is selected by default.")
            continue
        return MetricsMode(mapped)


def prompt_ask_send(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] | None = None,
) -> bool:
    write = output_fn or (lambda text: print(text, file=sys.stderr))
    write("Send anonymous execution metrics for this run?")
    raw = input_fn("Send metrics? [y/N]: ").strip().lower()
    return raw in {"y", "yes"}


def consent_needed(config: UserConfig | None = None) -> bool:
    current = config if config is not None else load_user_config()
    return not current.metrics.consent_completed


def ensure_consent(
    *,
    interactive: bool | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] | None = None,
    config_path: Path | None = None,
) -> UserConfig:
    """Show first-run consent once. No metrics are sent here."""

    path = config_path or default_config_path()
    current = load_user_config(path)
    if current.metrics.consent_completed and current.metrics.mode is not None:
        return current
    use_interactive = sys.stdin.isatty() if interactive is None else interactive
    if not use_interactive:
        return current
    mode = prompt_consent(input_fn=input_fn, output_fn=output_fn)
    return set_metrics_mode(mode, path=path, consent_completed=True)


class TelemetryService:
    """Coordinates consent checks and non-blocking metrics submission."""

    def __init__(
        self,
        *,
        transport: TelemetryTransport | None = None,
        config_path: Path | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.transport = transport or default_transport()
        self.config_path = config_path
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="joymesh-metrics"
        )
        self._owns_executor = executor is None
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def load_settings(self) -> MetricsSettings:
        return load_user_config(self.config_path).metrics

    def status(self) -> dict[str, Any]:
        settings = self.load_settings()
        return {
            "mode": settings.mode.value if settings.mode else None,
            "consent_completed": settings.consent_completed,
            "config_path": str(self.config_path or default_config_path()),
            "endpoint_configured": default_telemetry_endpoint() is not None
            or not isinstance(self.transport, NullTelemetryTransport),
        }

    def set_mode(self, mode: MetricsMode) -> UserConfig:
        return set_metrics_mode(mode, path=self.config_path, consent_completed=True)

    def ensure_first_run_consent(self, *, interactive: bool | None = None) -> UserConfig:
        return ensure_consent(
            interactive=interactive,
            config_path=self.config_path,
        )

    def transmission_allowed(self) -> bool:
        """True only when consent is complete and mode is not never."""

        settings = self.load_settings()
        return bool(
            settings.consent_completed
            and settings.mode is not None
            and settings.mode is not MetricsMode.NEVER
        )

    def maybe_send(
        self,
        report: AnonymousExecutionMetrics | AnonymousExecutionReport,
        *,
        interactive: bool | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] | None = None,
    ) -> Future[None] | None:
        """Send according to preference. Never raises into the caller."""

        try:
            settings = self.load_settings()
            if not settings.consent_completed or settings.mode is None:
                return None
            if settings.mode is MetricsMode.NEVER:
                return None
            if settings.mode is MetricsMode.ASK:
                use_interactive = sys.stdin.isatty() if interactive is None else interactive
                if not use_interactive:
                    return None
                if not prompt_ask_send(input_fn=input_fn, output_fn=output_fn):
                    return None
            metrics = (
                report if isinstance(report, AnonymousExecutionMetrics) else report.to_metrics()
            )
            return self._submit(metrics)
        except Exception:
            return None

    def _submit(self, report: AnonymousExecutionMetrics) -> Future[None] | None:
        def _run() -> None:
            try:
                self.transport.send(report)
            except (URLError, TimeoutError, OSError, Exception):
                return

        try:
            return self._executor.submit(_run)
        except Exception:
            return None


_default_service: TelemetryService | None = None
_default_lock = threading.Lock()


def get_telemetry_service() -> TelemetryService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = TelemetryService()
        return _default_service


def reset_telemetry_service_for_tests() -> None:
    global _default_service
    with _default_lock:
        if _default_service is not None:
            _default_service.close()
        _default_service = None


__all__ = [
    "METRICS_PAYLOAD_ALLOWLIST",
    "PAYLOAD_ALLOWLIST",
    "QUALITY_ALLOWLIST",
    "REPORT_SCHEMA_VERSION",
    "TOKENS_ALLOWLIST",
    "USAGE_ALLOWLIST",
    "AnonymousExecutionMetrics",
    "AnonymousExecutionReport",
    "HttpTelemetryTransport",
    "MetricsMode",
    "MetricsSettings",
    "NullTelemetryTransport",
    "RecordingTelemetryTransport",
    "TelemetryMode",
    "TelemetryService",
    "TelemetrySettings",
    "TelemetryTransport",
    "UserConfig",
    "build_metrics_from_run",
    "build_report_from_execution",
    "build_report_from_run",
    "consent_needed",
    "ensure_consent",
    "get_telemetry_service",
    "joymesh_version",
    "load_user_config",
    "model_family",
    "platform_label",
    "preview_metrics_placeholder",
    "preview_report_placeholder",
    "prompt_ask_send",
    "prompt_consent",
    "render_consent_dialog",
    "render_preview_yaml",
    "reset_telemetry_service_for_tests",
    "save_user_config",
    "set_metrics_mode",
    "set_telemetry_mode",
]
