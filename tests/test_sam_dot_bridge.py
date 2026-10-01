"""Simulated contract checks, never evidence of hosted dot execution."""

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from joymesh.runtime_v1.contracts.workers import (
    ExecutionLeaseToken,
    ExecutionOffer,
    FactualExecutionResult,
)

spec = spec_from_file_location(
    "sam_dot_bridge", Path(__file__).parents[1] / "examples/sam_dot_bridge.py"
)
bridge_module = module_from_spec(spec)
sys.modules[spec.name] = bridge_module
spec.loader.exec_module(bridge_module)
Bridge = bridge_module.Bridge
TransportContext = bridge_module.TransportContext
ReportInbox = bridge_module.ReportInbox


class MockJoyCtl:
    """Explicit test authority; this is not a production approval implementation."""

    def __init__(self):
        self.calls = []
        self.grant_consumed = False
        self.expired = False
        self.revoked = False
        self.current_generation = 2
        self.current_fence = 9
        self.graph_version = 3
        self.graph_race = False
        self.results = {}
        self.now = datetime.now(UTC)
        self.offer = ExecutionOffer(
            "exec-1",
            "attempt-1",
            "worker-server-issued",
            "mock-dot",
            "safe fixture",
            "/fixture",
            ExecutionLeaseToken(
                "lease-1",
                "worker-server-issued",
                "exec-1",
                "attempt-1",
                2,
                9,
                self.now + timedelta(minutes=1),
            ),
            offer_id="offer-1",
        )

    def request(self, ctx, method, path, body=None):
        if ctx.organisation_id != "tenant-1" or ctx.subject != "owner-1":
            raise PermissionError("tenant or principal")
        self.calls.append((method, path, body))
        if path.endswith("/graph"):
            version = self.graph_version
            if self.graph_race:
                self.graph_version += 1
            return {"mission_id": "mission-1", "version": version}
        if path == "/api/executions" and method == "POST":
            if body["authorization_id"] != "ctl-grant" or body["prompt_id"] != "ctl-prompt":
                return {"state": "approval_required", "execution_performed": False}
            if self.grant_consumed:
                return {"state": "already_submitted", "execution_id": "exec-1"}
            self.grant_consumed = True
            return {"state": "queued", "execution_id": "exec-1"}
        return {"state": "draft", "mission_id": "mission-1"}

    def assignment(self, ctx, offer_id):
        if (ctx.organisation_id, ctx.subject, ctx.session, offer_id) != (
            "tenant-1",
            "owner-1",
            "enrolled-session",
            "offer-1",
        ) or self.revoked:
            raise PermissionError("assignment or enrollment")
        if (
            not self.grant_consumed
            or self.expired
            or self.offer.lease.expires_at <= datetime.now(UTC)
            or self.offer.lease.generation != self.current_generation
            or self.offer.lease.fencing_token != self.current_fence
        ):
            raise PermissionError("current authority or lease required")
        return self.offer

    def record_result(self, ctx, offer, result):
        # Fixture models existing authoritative consumption, not bridge state.
        self.assignment(ctx, offer.offer_id)
        key = (result.execution_id, result.attempt_id)
        if key in self.results:
            if self.results[key] != result:
                raise ValueError("divergent replay")
            return {"state": "duplicate", "mission_completed": None}
        self.results[key] = result
        return {
            "state": "result_recorded",
            "mission_completed": None,
            "evidence": list(result.artifact_references),
        }


@pytest.fixture
def setup():
    ctl = MockJoyCtl()
    ctx = TransportContext("tenant-1", "owner-1", "enrolled-session")
    return ctl, Bridge(ctl), ctx


def submit(bridge, ctx):
    return bridge.call(
        ctx,
        "execution_submit",
        {
            "mission_id": "mission-1",
            "step_id": "step-1",
            "authorization_id": "ctl-grant",
            "prompt_id": "ctl-prompt",
        },
    )


def result(ctl):
    return FactualExecutionResult(
        "exec-1",
        "attempt-1",
        "worker-server-issued",
        "mock-dot",
        ctl.now,
        ctl.now,
        0,
        "exited",
        artifact_references=("fixture://evidence/test-output",),
    )


def test_simulated_round_trip_keeps_acceptance_distinct_from_completion(setup):
    ctl, bridge, ctx = setup
    assert (
        bridge.call(
            ctx,
            "intent_submit",
            {"workspace_id": "ws", "project_id": "project", "title": "safe fixture"},
        )["state"]
        == "draft"
    )
    assert submit(bridge, ctx)["state"] == "queued"
    returned = bridge.receive_result(ctx, "offer-1", result(ctl))
    assert returned["state"] == "result_recorded"
    assert returned["mission_completed"] is None
    assert returned["evidence"] == ["fixture://evidence/test-output"]
    assert result(ctl).as_dict()["verification_passed"] is None


def test_grant_replay_does_not_execute_twice(setup):
    _, bridge, ctx = setup
    submit(bridge, ctx)
    assert submit(bridge, ctx)["state"] == "already_submitted"


def test_missing_grant_is_rejected_without_upstream_call(setup):
    ctl, bridge, ctx = setup
    with pytest.raises(ValueError):
        bridge.call(ctx, "execution_submit", {"mission_id": "mission-1"})
    assert not ctl.calls


def test_approval_required_is_preserved(setup):
    _, bridge, ctx = setup
    value = bridge.call(
        ctx,
        "execution_submit",
        {
            "mission_id": "mission-1",
            "step_id": "step-1",
            "authorization_id": "unapproved",
            "prompt_id": "ctl-prompt",
        },
    )
    assert value == {"state": "approval_required", "execution_performed": False}


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_id", "CoS"),
        ("attempt_id", "other"),
        ("execution_id", "other"),
        ("harness", "real-dot"),
    ],
)
def test_wrong_assignment_return(setup, field, value):
    ctl, bridge, ctx = setup
    submit(bridge, ctx)
    with pytest.raises(PermissionError):
        bridge.receive_result(ctx, "offer-1", replace(result(ctl), **{field: value}))
    assert not ctl.results


@pytest.mark.parametrize(
    "ctx",
    [
        TransportContext("foreign", "owner-1", "enrolled-session"),
        TransportContext("tenant-1", "CoS", "enrolled-session"),
        TransportContext("tenant-1", "owner-1", "other-session"),
    ],
)
def test_identity_and_tenant_bound_returns(setup, ctx):
    ctl, bridge, owner = setup
    submit(bridge, owner)
    with pytest.raises(PermissionError):
        bridge.receive_result(ctx, "offer-1", result(ctl))


@pytest.mark.parametrize("flag", ["expired", "revoked"])
def test_stale_lease_or_disconnect_rejected_even_on_duplicate(setup, flag):
    ctl, bridge, ctx = setup
    submit(bridge, ctx)
    bridge.receive_result(ctx, "offer-1", result(ctl))
    setattr(ctl, flag, True)
    with pytest.raises(PermissionError):
        bridge.receive_result(ctx, "offer-1", result(ctl))


def test_result_replay_and_conflict(setup):
    ctl, bridge, ctx = setup
    submit(bridge, ctx)
    bridge.receive_result(ctx, "offer-1", result(ctl))
    assert bridge.receive_result(ctx, "offer-1", result(ctl))["state"] == "duplicate"
    with pytest.raises(ValueError):
        bridge.receive_result(ctx, "offer-1", replace(result(ctl), exit_code=1))


@pytest.mark.parametrize("field", ["current_generation", "current_fence"])
def test_superseded_assignment_fencing_rejects_return(setup, field):
    ctl, bridge, ctx = setup
    submit(bridge, ctx)
    setattr(ctl, field, getattr(ctl, field) + 1)
    with pytest.raises(PermissionError):
        bridge.receive_result(ctx, "offer-1", result(ctl))
    assert not ctl.results


def test_expired_lease_timestamp(setup):
    ctl, bridge, ctx = setup
    submit(bridge, ctx)
    ctl.offer = replace(
        ctl.offer, lease=replace(ctl.offer.lease, expires_at=ctl.now - timedelta(seconds=1))
    )
    with pytest.raises(PermissionError):
        bridge.receive_result(ctx, "offer-1", result(ctl))


def test_input_does_not_override_transport_identity_or_inject_policy(setup):
    _, bridge, ctx = setup
    for injected in [{"dot_name": "Tech"}, {"policy_grant": "allow"}, {"subject": "owner-1"}]:
        with pytest.raises(ValueError):
            bridge.call(ctx, "mission_fetch", {"mission_id": "m", **injected})


def test_supported_routes_and_path_encoding(setup):
    ctl, bridge, ctx = setup
    for name, suffix in [
        ("mission_fetch", ""),
        ("evidence_fetch", "/evidence"),
        ("verification_fetch", "/verification"),
    ]:
        bridge.call(ctx, name, {"mission_id": "a/b"})
        assert ctl.calls[-1][1] == "/api/missions/a%2Fb" + suffix
    bridge.call(ctx, "status_fetch", {"execution_id": "exec-1"})
    assert ctl.calls[-1][1] == "/api/executions/exec-1"


def test_mcp_unavailable_surfaces_fail_closed(setup):
    ctl, bridge, ctx = setup
    assert bridge.rpc(ctx, {"id": 1, "method": "events/list"})["result"] == {"events": []}
    assert (
        "events"
        not in bridge.rpc(ctx, {"id": 2, "method": "server/discover"})["result"]["capabilities"]
    )
    assert bridge.rpc(ctx, {"id": 3, "method": "events/subscribe"})["result"]["state"] == "blocked"
    assert (
        bridge.call(
            ctx, "report_acknowledge", {"mission_id": "m", "report_version": "1", "event_id": "e"}
        )["state"]
        == "blocked"
    )
    assert not ctl.calls


def test_mcp_tools_discovery_and_call(setup):
    _, bridge, ctx = setup
    tools = bridge.rpc(ctx, {"id": 1, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "intent_submit",
        "execution_submit",
        "mission_fetch",
        "status_fetch",
        "evidence_fetch",
        "verification_fetch",
        "report_projection_fetch",
    }
    response = bridge.rpc(
        ctx,
        {
            "id": 2,
            "method": "tools/call",
            "params": {"name": "mission_fetch", "arguments": {"mission_id": "mission-1"}},
        },
    )
    assert response["result"]["structuredContent"]["mission_id"] == "mission-1"


def test_supported_report_projection_preserves_provenance(setup):
    _, bridge, ctx = setup
    projection = bridge.call(ctx, "report_projection_fetch", {"mission_id": "mission-1"})
    assert projection["projection_version"] == 3
    assert projection["canonical_report"] is False
    assert projection["snapshot_atomic"] is False


def test_report_projection_detects_graph_change(setup):
    ctl, bridge, ctx = setup
    ctl.graph_race = True
    assert (
        bridge.call(ctx, "report_projection_fetch", {"mission_id": "mission-1"})["state"]
        == "blocked"
    )


def test_report_projection_cannot_fetch_foreign_tenant(setup):
    _, bridge, _ = setup
    with pytest.raises(PermissionError):
        bridge.call(
            TransportContext("foreign", "owner-1"),
            "report_projection_fetch",
            {"mission_id": "mission-1"},
        )


def test_report_versions_duplicates_order_and_restart(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    inbox = ReportInbox(path)
    report = {"mission_id": "m", "state": "completed", "evidence": ["fixture://output"]}
    assert inbox.observe("tenant", "m", 2, report) == "new"
    assert inbox.observe("tenant", "m", 2, report) == "duplicate"
    old = {"mission_id": "m", "state": "approval_required"}
    assert inbox.observe("tenant", "m", 1, old) == "out_of_order"
    assert inbox.latest("tenant", "m") == report
    assert inbox.latest("foreign", "m") is None
    with pytest.raises(ValueError):
        inbox.observe("tenant", "m", 2, old)
    inbox.acknowledge_receipt("tenant", "m", 2)
    inbox.acknowledge_receipt("tenant", "m", 2)
    inbox.db.close()
    restarted = ReportInbox(path)
    assert restarted.latest("tenant", "m") == report
    row = restarted.db.execute("SELECT acknowledged FROM receipts WHERE version=2").fetchone()
    assert row[0] == 1
    restarted.db.close()
