"""Connector-neutral local live-test runner built on ConnectorRuntime."""

from __future__ import annotations

import asyncio
import os
import time
import warnings
from pathlib import Path

from joymesh.runtime_v1.connector_protocol import (
    ConnectorExecutionContext,
    ConnectorLiveTestResult,
    ConnectorRuntime,
    ConnectorRuntimeEvent,
    ConnectorRuntimeNotice,
)
from joymesh.runtime_v1.connectors import get_connector


async def run_connector_live_test(
    *,
    connector: ConnectorRuntime,
    workspace: Path,
    prompt: str,
    timeout_seconds: float = 180.0,
    node_id: str = "live-test",
) -> ConnectorLiveTestResult:
    """Run discovery → auth → read-only cert argv → process → parse for any connector."""

    started = time.perf_counter()
    notices: list[ConnectorRuntimeNotice] = []
    events: list[ConnectorRuntimeEvent] = []
    context = ConnectorExecutionContext(
        node_id=node_id,
        workspace_path=str(workspace),
    )

    preflight = connector.adapter_verification_notice()
    if preflight is not None:
        notices.append(preflight)

    discovery = await connector.discover(context)
    if not discovery.installed or not discovery.usable or not discovery.executable_path:
        reason = discovery.reason_code or "connector_not_installed"
        notices.append(
            ConnectorRuntimeNotice(
                event_type="connector_unavailable",
                connector_id=connector.connector_id,
                display_name=connector.display_name,
                reason_code=reason,
                message=str(discovery.details.get("detail") or reason),
                recoverable=True,
                recommended_action="install_or_repair",
            )
        )
        return ConnectorLiveTestResult(
            connector_id=connector.connector_id,
            display_name=connector.display_name,
            installed=discovery.installed,
            usable=False,
            authenticated=None,
            discovery_reason_code=discovery.reason_code,
            executable_path=discovery.executable_path,
            version=discovery.version,
            fingerprint=discovery.fingerprint,
            exit_code=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            certification_passed=False,
            notices=tuple(notices),
            events=(),
            error=str(discovery.details.get("detail") or reason),
        )

    auth_status: str | None = None
    authenticated: bool | None = None
    billing_risk: str | None = None
    try:
        evidence = await connector.verify_authentication(context)
        auth_status = evidence.status
        authenticated = evidence.status == "authenticated"
        billing_risk = (
            str(evidence.details.get("billing_risk"))
            if evidence.details.get("billing_risk")
            else None
        )
        if evidence.status == "unauthenticated":
            notices.append(
                ConnectorRuntimeNotice(
                    event_type="connector_auth_required",
                    connector_id=connector.connector_id,
                    display_name=connector.display_name,
                    reason_code="connector_auth_required",
                    message="Connector authentication is required before certification.",
                    recoverable=True,
                    recommended_action="authenticate",
                )
            )
        elif evidence.status in {"expired", "plan_restricted", "quota_exhausted"}:
            notices.append(
                ConnectorRuntimeNotice(
                    event_type="connector_plan_restriction",
                    connector_id=connector.connector_id,
                    display_name=connector.display_name,
                    reason_code=f"connector_{evidence.status}",
                    message=str(evidence.details.get("detail") or evidence.status),
                    recoverable=True,
                    recommended_action="reauthenticate_or_upgrade",
                )
            )
    except Exception as exc:
        auth_status = "unknown"
        authenticated = None
        notices.append(
            ConnectorRuntimeNotice(
                event_type="connector_auth_required",
                connector_id=connector.connector_id,
                display_name=connector.display_name,
                reason_code="connector_auth_unknown",
                message=str(exc),
                recoverable=True,
                recommended_action="retry_auth_inspection",
            )
        )

    if authenticated is False:
        return ConnectorLiveTestResult(
            connector_id=connector.connector_id,
            display_name=connector.display_name,
            installed=True,
            usable=True,
            authenticated=False,
            auth_status=auth_status,
            discovery_reason_code=discovery.reason_code,
            executable_path=discovery.executable_path,
            version=discovery.version,
            fingerprint=discovery.fingerprint,
            exit_code=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            certification_passed=False,
            notices=tuple(notices),
            events=(),
            error="authentication required",
        )

    if (
        authenticated
        and billing_risk == "api_key_possibly_billable"
        and os.environ.get("JOYMESH_APPROVE_API_BILLING") != "1"
    ):
        notices.append(
            ConnectorRuntimeNotice(
                event_type="connector_plan_restriction",
                connector_id=connector.connector_id,
                display_name=connector.display_name,
                reason_code="connector_billing_approval_required",
                message=(
                    "API-key billing route detected; set JOYMESH_APPROVE_API_BILLING=1 "
                    "to approve live inference charges."
                ),
                recoverable=True,
                recommended_action="approve_billing_or_use_subscription",
            )
        )
        return ConnectorLiveTestResult(
            connector_id=connector.connector_id,
            display_name=connector.display_name,
            installed=True,
            usable=True,
            authenticated=True,
            auth_status=auth_status,
            discovery_reason_code=discovery.reason_code,
            executable_path=discovery.executable_path,
            version=discovery.version,
            fingerprint=discovery.fingerprint,
            exit_code=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            certification_passed=False,
            notices=tuple(notices),
            events=(),
            error="billing approval required",
        )

    argv = list(
        connector.build_read_only_cert_argv(
            executable=discovery.executable_path,
            prompt=prompt,
            workspace=workspace,
        )
    )
    env = {
        **os.environ,
        **dict(connector.execution_environment(read_only=True)),
    }
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            from joymesh.connectors.process_utils import terminate_process_tree

            await terminate_process_tree(process)
            notices.append(
                ConnectorRuntimeNotice(
                    event_type="connector_unavailable",
                    connector_id=connector.connector_id,
                    display_name=connector.display_name,
                    reason_code="connector_timeout",
                    message=f"Live test timed out after {timeout_seconds}s",
                    recoverable=True,
                    recommended_action="retry_with_higher_timeout",
                )
            )
            return ConnectorLiveTestResult(
                connector_id=connector.connector_id,
                display_name=connector.display_name,
                installed=True,
                usable=True,
                authenticated=authenticated,
                auth_status=auth_status,
                discovery_reason_code=discovery.reason_code,
                executable_path=discovery.executable_path,
                version=discovery.version,
                fingerprint=discovery.fingerprint,
                exit_code=None,
                duration_ms=int((time.perf_counter() - started) * 1000),
                certification_passed=False,
                notices=tuple(notices),
                events=(),
                error="timeout",
            )
    except OSError as exc:
        notices.append(
            ConnectorRuntimeNotice(
                event_type="connector_unavailable",
                connector_id=connector.connector_id,
                display_name=connector.display_name,
                reason_code="connector_executable_broken",
                message=str(exc),
                recoverable=True,
                recommended_action="repair_executable",
            )
        )
        return ConnectorLiveTestResult(
            connector_id=connector.connector_id,
            display_name=connector.display_name,
            installed=True,
            usable=False,
            authenticated=authenticated,
            auth_status=auth_status,
            discovery_reason_code="broken_executable",
            executable_path=discovery.executable_path,
            version=discovery.version,
            fingerprint=discovery.fingerprint,
            exit_code=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            certification_passed=False,
            notices=tuple(notices),
            events=(),
            error=str(exc),
        )

    output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
    exit_code = process.returncode if process.returncode is not None else 1
    raw_events = connector.parse_events(output)
    for index, item in enumerate(raw_events, start=1):
        event_type = str(item.get("event_type") or item.get("type") or "message.output")
        events.append(
            ConnectorRuntimeEvent(
                event_type=event_type,
                sequence=index,
                payload={key: value for key, value in item.items() if key != "event_type"},
            )
        )
        notice = _notice_from_event(connector, item)
        if notice is not None:
            notices.append(notice)

    if not raw_events and output.strip():
        notices.append(
            ConnectorRuntimeNotice(
                event_type="connector_unavailable",
                connector_id=connector.connector_id,
                display_name=connector.display_name,
                reason_code="connector_output_unparseable",
                message="Connector produced output that could not be parsed into events",
                recoverable=True,
                recommended_action="inspect_raw_output",
            )
        )

    certification_passed = exit_code == 0 and any(
        item.event_type in {"run.completed", "task.succeeded", "step_finish", "text"}
        or item.payload.get("type") in {"step_finish", "text", "run.completed"}
        for item in events
    )
    if exit_code == 0 and not events and not output.strip():
        certification_passed = True
    if exit_code != 0:
        certification_passed = False
        if not any(item.reason_code.startswith("connector_") for item in notices):
            notices.append(
                ConnectorRuntimeNotice(
                    event_type="connector_unavailable",
                    connector_id=connector.connector_id,
                    display_name=connector.display_name,
                    reason_code="connector_execution_failed",
                    message=f"Process exited with {exit_code}",
                    recoverable=True,
                    recommended_action="retry",
                )
            )

    return ConnectorLiveTestResult(
        connector_id=connector.connector_id,
        display_name=connector.display_name,
        installed=True,
        usable=True,
        authenticated=authenticated,
        auth_status=auth_status,
        discovery_reason_code=discovery.reason_code,
        executable_path=discovery.executable_path,
        version=discovery.version,
        fingerprint=discovery.fingerprint,
        exit_code=exit_code,
        duration_ms=int((time.perf_counter() - started) * 1000),
        certification_passed=certification_passed,
        notices=tuple(notices),
        events=tuple(events),
        error=None if certification_passed else (output.strip()[:500] or f"exit {exit_code}"),
    )


def _notice_from_event(
    connector: ConnectorRuntime, item: dict[str, object] | object
) -> ConnectorRuntimeNotice | None:
    if not isinstance(item, dict):
        return None
    blob = " ".join(str(value) for value in item.values()).lower()
    text = str(item.get("text") or item.get("message") or blob)
    lowered = text.lower()
    if "rate limit" in lowered or "too many requests" in lowered:
        code = "connector_rate_limited"
        event_type = "connector_plan_restriction"
    elif "quota" in lowered or "usage limit" in lowered:
        code = "connector_quota_exhausted"
        event_type = "connector_plan_restriction"
    elif "not authenticated" in lowered or "login required" in lowered:
        code = "connector_auth_required"
        event_type = "connector_auth_required"
    elif item.get("event_type") == "permission.denied" or item.get("reason_code"):
        reason = str(item.get("reason_code") or "permission_denied")
        return ConnectorRuntimeNotice(
            event_type="permission.denied",
            connector_id=connector.connector_id,
            display_name=connector.display_name,
            reason_code=reason,
            message=str(item.get("error") or item.get("text") or reason)[:300],
            recoverable=True,
            recommended_action="continue_read_only",
        )
    else:
        return None
    return ConnectorRuntimeNotice(
        event_type=event_type,
        connector_id=connector.connector_id,
        display_name=connector.display_name,
        reason_code=code,
        message=text[:300],
        recoverable=True,
        recommended_action="wait_or_reauthenticate",
    )


def run_cursor_live_test(
    *,
    control_plane_url: str = "http://127.0.0.1:8787",
    node_id: str = "local",
    enable_routing: bool = False,
    workspace: Path | None = None,
    prompt: str | None = None,
    timeout_seconds: float = 180.0,
) -> dict[str, object]:
    """Deprecated Cursor-named wrapper around the connector-neutral live test."""

    del control_plane_url, enable_routing
    warnings.warn(
        "run_cursor_live_test is deprecated; use run_connector_live_test with get_connector(...)",
        DeprecationWarning,
        stacklevel=2,
    )
    connector = get_connector("cursor")
    target = workspace or Path.cwd()
    cert_prompt = prompt or (
        "Read README.md if present and summarise the repository without modifying files."
    )
    result = asyncio.run(
        run_connector_live_test(
            connector=connector,
            workspace=target,
            prompt=cert_prompt,
            timeout_seconds=timeout_seconds,
            node_id=node_id,
        )
    )
    payload = result.as_dict()
    payload["status"] = "ready" if result.certification_passed else "failed"
    payload["deprecated_wrapper"] = True
    return payload


def render_live_test_result(result: ConnectorLiveTestResult) -> str:
    """Human-readable rendering of a connector-neutral live-test result."""

    lines = [
        f"connector: {result.display_name} ({result.connector_id})",
        f"installed: {result.installed}",
        f"usable: {result.usable}",
        f"authenticated: {result.authenticated}",
        f"auth_status: {result.auth_status}",
        f"discovery_reason_code: {result.discovery_reason_code}",
        f"executable_path: {result.executable_path}",
        f"version: {result.version}",
        f"exit_code: {result.exit_code}",
        f"duration_ms: {result.duration_ms}",
        f"certification_passed: {result.certification_passed}",
    ]
    if result.error:
        lines.append(f"error: {result.error}")
    if result.notices:
        lines.append("notices:")
        for notice in result.notices:
            lines.append(
                f"  - {notice.reason_code}: {notice.message} (recoverable={notice.recoverable})"
            )
    if result.events:
        lines.append(f"events: {len(result.events)}")
        for event in result.events[:10]:
            lines.append(f"  - [{event.sequence}] {event.event_type}")
    return "\n".join(lines)
