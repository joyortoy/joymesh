from __future__ import annotations

import pytest

from joymesh.control_plane.contracts import (
    OnboardingState,
    RemoteTaskEnvelope,
    WorkspaceGrant,
)
from joymesh.control_plane.security import generate_node_keypair, pkce_pair
from joymesh.control_plane.service import ControlPlane


@pytest.mark.asyncio
async def test_pairing_onboarding_remote_task_and_revocation(tmp_path) -> None:
    control = ControlPlane()
    _verifier, challenge = pkce_pair()
    pairing, device_code = await control.begin_pairing(
        organisation_id="org",
        workspace_id="workspace",
        code_challenge=challenge,
    )
    await control.approve_pairing(pairing.id, user_id="user")
    _node_private, node_public = generate_node_keypair()
    node = await control.register_node(
        pairing.id,
        device_code=device_code,
        name="MacBook",
        public_key=node_public,
        key_id="node-1",
        platform="darwin",
        version="0.1.0",
    )
    progress = await control.set_onboarding_state(
        user_id="user",
        organisation_id="org",
        workspace_id="workspace",
        state=OnboardingState.ENVIRONMENT_CHECK,
        node_id=node.id,
    )
    assert progress.state is OnboardingState.ENVIRONMENT_CHECK

    await control.grant_workspace(
        WorkspaceGrant(
            workspace_id="workspace",
            node_id=node.id,
            root_path=str(tmp_path),
            allow_write=True,
        ),
        actor_id="user",
    )
    control_private, _ = generate_node_keypair()
    task = await control.create_remote_task(
        RemoteTaskEnvelope(
            organisation_id="org",
            workspace_id="workspace",
            node_id=node.id,
            user_id="user",
            browser_session_id="session",
            harness_id="codex",
            task="Create a summary",
            key_id="control-1",
        ),
        signing_private_key=control_private,
    )
    assert task.signature
    assert any(event.action == "remote_task.create" for event in control.store.audit)

    await control.revoke_node(node.id, actor_id="user")
    with pytest.raises(PermissionError, match="revoked"):
        await control.create_remote_task(
            task.model_copy(update={"id": "another", "nonce": "fresh"}),
            signing_private_key=control_private,
        )
