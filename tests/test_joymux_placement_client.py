"""JoyMux placement client used by ``joymesh run``."""

from __future__ import annotations

from joymesh.joymux_placement import build_place_params, fetch_context_placement


def test_build_place_params_defaults_auto_to_grok() -> None:
    params = build_place_params(
        harness="auto",
        workspace="/tmp/ws",
        task="imagine a dashboard",
    )
    assert params["requirements"]["eligible_harnesses"] == ["grok"]
    assert params["runtime_facts"]["candidates"][0]["harness"] == "grok"
    assert params["runtime_facts"]["candidates"][0]["eligible"] is True


def test_build_place_params_keeps_requested_harness() -> None:
    params = build_place_params(
        harness="codex",
        workspace="/tmp/ws",
        task="edit",
        requirements_id="req_fixed",
    )
    assert params["requirements"]["requirements_id"] == "req_fixed"
    assert params["requirements"]["eligible_harnesses"] == ["codex"]
    assert params["runtime_facts"]["candidates"][0]["harness"] == "codex"


def test_fetch_context_placement_live_when_daemon_up() -> None:
    import pytest

    from joymesh.joymux_placement import JoyMuxPlacementError, resolve_joymux_socket

    sock = resolve_joymux_socket()
    if not sock.exists():
        pytest.skip("JoyMux runtime.sock not present")
    try:
        decision = fetch_context_placement(
            harness="grok",
            workspace="/tmp/joyui-place-test",
            task="placement probe",
            client_name="joymesh-test",
        )
    except JoyMuxPlacementError as exc:
        if exc.code in {"joymux_connect_failed", "joymux_socket_missing"}:
            pytest.skip(str(exc))
        raise
    assert decision.get("schema") == "joy.context_placement_decision/v1"
    assert decision.get("executable") is True
    assert decision.get("selected_harness") == "grok"
