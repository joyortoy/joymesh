"""Guided production live-test helper for the Cursor connector path."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any


def run_cursor_live_test(
    *,
    control_plane_url: str,
    node_id: str,
    enable_routing: bool = False,
) -> dict[str, Any]:
    base = control_plane_url.rstrip("/")
    report: dict[str, Any] = {
        "connector_id": "cursor",
        "node_id": node_id,
        "status": "preflight",
        "steps": [],
    }

    def step(name: str, **payload: Any) -> None:
        report["steps"].append({"step": name, **payload})

    executable = shutil.which("cursor-agent")
    if not executable:
        report["status"] = "blocked"
        report["blocking_reason"] = "cursor-agent executable not found on PATH"
        return report
    version = _run_local([executable, "--version"])
    step("cursor_version", executable=executable, version=version.strip())
    auth = _run_local([executable, "status"])
    authenticated = "logged in" in auth.lower() and "not logged" not in auth.lower()
    step("cursor_auth_status", authenticated=authenticated, redacted=True)
    if not authenticated:
        report["status"] = "authentication_required"
        report["user_action"] = (
            "Run `cursor-agent login` on this Mac, complete browser sign-in, "
            "then re-run: joymesh connector live-test cursor --profile read-only "
            f"--node-id {node_id}"
        )
        return report

    session = _get_json(f"{base}/nodes/{node_id}/session")
    step("node_session", session=session)
    if session is None:
        report["status"] = "blocked"
        report["blocking_reason"] = (
            "No authenticated node WebSocket session. Pair and connect the JoyMesh Node first."
        )
        return report

    readiness = _get_json(f"{base}/nodes/{node_id}/connectors/cursor/readiness")
    step("initial_readiness", readiness=readiness)
    report["readiness"] = readiness

    # Discover when needed
    state = readiness.get("state")
    if state in {None, "not_available", "available_to_install"}:
        task = _plan_and_approve(base, node_id, "discover")
        step("discover", task=task)
        readiness = _wait_readiness(base, node_id, not_in={"not_available"})
        report["readiness"] = readiness
        state = readiness.get("state")

    if state == "authentication_required":
        report["status"] = "authentication_required"
        report["user_action"] = (
            "Approve authenticate plan in the UI or API, complete login on the Mac, "
            "then click 'I completed login' or wait for status polling."
        )
        return report

    if state in {"verification_required", "authenticated"}:
        print(
            "\nThis verification may use a small amount of your Cursor plan allowance.\n"
            "It will run a bounded non-writing request using Cursor Agent.\n"
        )
        confirm = input("Approve adapter verification? [y/N]: ").strip().lower()
        if confirm != "y":
            report["status"] = "approval_required"
            report["blocking_reason"] = "adapter verification not approved"
            return report
        task = _plan_and_approve(base, node_id, "verify-adapter")
        step("verify_adapter", task=task)
        readiness = _wait_readiness(
            base, node_id, want_in={"certification_required", "routing_disabled", "ready"}
        )
        report["readiness"] = readiness
        state = readiness.get("state")

    if state == "certification_required":
        print(
            "\nRead-only certification will create an isolated temporary Git repository,\n"
            "run cursor-agent --print --output-format stream-json, and verify no file changes.\n"
            "This may use a small amount of your Cursor plan allowance.\n"
        )
        confirm = input("Approve read-only certification? [y/N]: ").strip().lower()
        if confirm != "y":
            report["status"] = "approval_required"
            report["blocking_reason"] = "certification not approved"
            return report
        task = _plan_and_approve(base, node_id, "certify")
        step("certify", task=task)
        readiness = _wait_readiness(
            base, node_id, want_in={"routing_disabled", "ready", "certification_failed"}
        )
        report["readiness"] = readiness
        state = readiness.get("state")

    if state == "routing_disabled":
        report["status"] = "routing_disabled"
        report["routing_profile"] = "cursor_read_only"
        if enable_routing:
            confirm = input("Enable read-only routing now? [y/N]: ").strip().lower()
            if confirm != "y":
                report["blocking_reason"] = "routing enablement declined"
                return report
            enabled = _post_json(f"{base}/nodes/{node_id}/connectors/cursor/routing/enable")
            step("enable_routing", readiness=enabled)
            report["readiness"] = enabled
            report["status"] = enabled.get("state", "ready")
        return report

    if state == "ready":
        report["status"] = "ready"
        report["routing_profile"] = readiness.get("routing_profile") or "cursor_read_only"
        return report

    report["status"] = state or "unknown"
    return report


def _plan_and_approve(base: str, node_id: str, action: str) -> dict[str, Any]:
    path = {
        "discover": f"/nodes/{node_id}/connectors/cursor/discover/plan",
        "verify-adapter": f"/nodes/{node_id}/connectors/cursor/verify-adapter/plan",
        "certify": f"/nodes/{node_id}/connectors/cursor/certify/plan",
        "verify-authentication": (f"/nodes/{node_id}/connectors/cursor/verify-authentication/plan"),
    }[action]
    planned = _post_json(f"{base}{path}", {"platform": "darwin"})
    plan = planned["plan"]
    executed = _post_json(
        f"{base}/connector-tasks/{plan['plan_id']}/execute",
        {"plan_hash": plan["plan_hash"], "approved": True},
    )
    return {"plan": plan, "task": executed}


def _wait_readiness(
    base: str,
    node_id: str,
    *,
    want_in: set[str] | None = None,
    not_in: set[str] | None = None,
    attempts: int = 60,
) -> dict[str, Any]:
    import time

    last: dict[str, Any] = {}
    for _ in range(attempts):
        last = _get_json(f"{base}/nodes/{node_id}/connectors/cursor/readiness")
        state = last.get("state")
        if want_in and state in want_in:
            return last
        if not_in and state not in not_in:
            return last
        time.sleep(1)
    return last


def _run_local(argv: list[str]) -> str:
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    return (completed.stdout or "") + (completed.stderr or "")


def _get_json(url: str) -> Any:
    try:
        with urllib.request.urlopen(url) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise RuntimeError(f"GET {url} failed: {exc.code} {body}") from exc


def _post_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload or {}).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise RuntimeError(f"POST {url} failed: {exc.code} {body}") from exc
