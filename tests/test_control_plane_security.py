from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature

from joymesh.control_plane.contracts import (
    ActionPlan,
    ApprovalDecision,
    PlanCommand,
    RemoteTaskEnvelope,
    RiskLevel,
)
from joymesh.control_plane.security import (
    NonceStore,
    ReplayDetectedError,
    bind_plan,
    generate_node_keypair,
    resolve_workspace_path,
    sign_envelope,
    verify_approval,
    verify_envelope,
)
from joymesh.models import utc_now


def test_signed_remote_task_detects_tampering_expiry_and_replay() -> None:
    private_key, public_key = generate_node_keypair()
    envelope = RemoteTaskEnvelope(
        organisation_id="org",
        workspace_id="workspace",
        node_id="node",
        user_id="user",
        browser_session_id="session",
        harness_id="codex",
        task="Inspect the repository",
        key_id="control-1",
    )
    signed = sign_envelope(envelope, private_key)
    verify_envelope(signed, public_key)

    with pytest.raises(InvalidSignature):
        verify_envelope(signed.model_copy(update={"task": "tampered"}), public_key)

    expired = signed.model_copy(update={"expires_at": utc_now() - timedelta(seconds=1)})
    with pytest.raises(PermissionError, match="expired"):
        verify_envelope(expired, public_key)

    nonces = NonceStore()
    nonces.consume(signed.nonce, signed.expires_at)
    with pytest.raises(ReplayDetectedError):
        nonces.consume(signed.nonce, signed.expires_at)


def test_approval_is_bound_to_exact_plan_and_session(tmp_path: Path) -> None:
    plan = bind_plan(
        ActionPlan(
            user_id="user",
            browser_session_id="session",
            node_id="node",
            harness_id="codex",
            action="install",
            command=PlanCommand(
                executable="npm",
                args=("install", "--global", "@openai/codex"),
                working_directory=str(tmp_path),
            ),
            risk=RiskLevel.HIGH,
        )
    )
    decision = ApprovalDecision(
        plan_id=plan.id,
        plan_hash=plan.plan_hash,
        user_id=plan.user_id,
        browser_session_id=plan.browser_session_id,
        node_id=plan.node_id,
        approved=True,
    )
    verify_approval(plan, decision)

    with pytest.raises(PermissionError, match="bound"):
        verify_approval(plan, decision.model_copy(update={"browser_session_id": "attacker"}))


def test_workspace_resolution_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()

    assert resolve_workspace_path(str(workspace), "src") == workspace / "src"
    with pytest.raises(PermissionError, match="escapes"):
        resolve_workspace_path(str(workspace), "../outside")

    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="escapes"):
        resolve_workspace_path(str(workspace), "escape/secret.txt")
