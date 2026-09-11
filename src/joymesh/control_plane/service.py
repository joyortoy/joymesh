"""SDK-first services for onboarding, pairing, approvals, and remote tasks."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

from joymesh.control_plane.contracts import (
    ActionPlan,
    ApprovalDecision,
    AuditEvent,
    NodeRegistration,
    OnboardingProgress,
    OnboardingState,
    PaidRoutePolicy,
    PairingSession,
    RemoteTaskEnvelope,
    WorkspaceGrant,
)
from joymesh.control_plane.onboarding_store import (
    InMemoryOnboardingProgressRepository,
    OnboardingConflictError,
    OnboardingProgressRepository,
    new_progress,
)
from joymesh.control_plane.security import NonceStore, sign_envelope, verify_approval
from joymesh.models import utc_now


@dataclass
class ControlPlaneStore:
    """Injectable store contract used by tests and the local reference service.

    Onboarding progress is NOT stored here in production — use
    ``OnboardingProgressRepository``. The ``onboarding`` dict remains only as a
    deprecated mirror for older tests that inspect ``store.onboarding`` directly.
    """

    onboarding: dict[str, OnboardingProgress] = field(default_factory=dict)
    pairings: dict[str, PairingSession] = field(default_factory=dict)
    nodes: dict[str, NodeRegistration] = field(default_factory=dict)
    grants: dict[str, WorkspaceGrant] = field(default_factory=dict)
    plans: dict[str, ActionPlan] = field(default_factory=dict)
    approvals: dict[str, ApprovalDecision] = field(default_factory=dict)
    tasks: dict[str, RemoteTaskEnvelope] = field(default_factory=dict)
    audit: list[AuditEvent] = field(default_factory=list)


class ControlPlane:
    """One orchestration seam shared by SDK, API, CLI, and WebSocket gateway."""

    def __init__(
        self,
        store: ControlPlaneStore | None = None,
        *,
        onboarding_repository: OnboardingProgressRepository | None = None,
    ) -> None:
        self.store = store or ControlPlaneStore()
        self.onboarding_repository: OnboardingProgressRepository = (
            onboarding_repository or InMemoryOnboardingProgressRepository()
        )
        self.nonces = NonceStore()

    async def onboarding_progress(
        self, *, user_id: str, organisation_id: str, workspace_id: str
    ) -> OnboardingProgress:
        progress = await self.onboarding_repository.get(user_id=user_id, workspace_id=workspace_id)
        if progress is None:
            progress = await self.onboarding_repository.put(
                progress=new_progress(
                    user_id=user_id,
                    organisation_id=organisation_id,
                    workspace_id=workspace_id,
                ).model_copy(update={"state": OnboardingState.ACCOUNT_READY}),
            )
        # Deprecated mirror for older tests.
        self.store.onboarding[f"{user_id}:{workspace_id}"] = progress
        return progress

    async def set_onboarding_state(
        self,
        *,
        user_id: str,
        organisation_id: str,
        workspace_id: str,
        state: OnboardingState,
        node_id: str | None = None,
        pairing_id: str | None = None,
        selected_harnesses: tuple[str, ...] | None = None,
        limited_mode_reason: str | None = None,
        paid_route_policy: PaidRoutePolicy | None = None,
        fireconnect_enabled: bool | None = None,
        last_error: str | None = None,
        expected_revision: int | None = None,
        clear_error: bool = False,
    ) -> OnboardingProgress:
        current = await self.onboarding_progress(
            user_id=user_id,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
        )
        completed = current.completed_steps
        if current.state not in completed and current.state is not OnboardingState.NOT_STARTED:
            completed = (*completed, current.state)
        selected = (
            selected_harnesses if selected_harnesses is not None else current.selected_harnesses
        )
        forbidden = {"fake", "joy"}
        if any(item in forbidden for item in selected):
            raise ValueError(
                "removed harness ids cannot be selected during onboarding: "
                + ", ".join(sorted(forbidden & set(selected)))
            )
        if selected_harnesses is not None:
            try:
                from joymesh.config import (
                    HarnessPreferences,
                    load_user_config,
                    save_harness_preferences,
                )

                prefs = load_user_config().harnesses
                save_harness_preferences(
                    HarnessPreferences(
                        enabled=tuple(dict.fromkeys(selected_harnesses)),
                        default=prefs.default if prefs.default in selected_harnesses else None,
                        custom=dict(prefs.custom),
                        selection_required=False,
                        migration_message=None,
                    )
                )
            except OSError:
                pass
        updated = current.model_copy(
            update={
                "organisation_id": organisation_id or current.organisation_id,
                "state": state,
                "node_id": node_id if node_id is not None else current.node_id,
                "pairing_id": pairing_id if pairing_id is not None else current.pairing_id,
                "selected_harnesses": selected,
                "completed_steps": completed,
                "limited_mode_reason": limited_mode_reason,
                "paid_route_policy": (
                    paid_route_policy
                    if paid_route_policy is not None
                    else current.paid_route_policy
                ),
                "fireconnect_enabled": (
                    fireconnect_enabled
                    if fireconnect_enabled is not None
                    else current.fireconnect_enabled
                ),
                "last_error": None
                if clear_error
                else (last_error if last_error is not None else current.last_error),
                "updated_at": utc_now(),
                "unsynchronised": False,
            }
        )
        try:
            stored = await self.onboarding_repository.put(
                progress=updated,
                expected_revision=expected_revision
                if expected_revision is not None
                else current.revision,
            )
        except OnboardingConflictError:
            raise
        self.store.onboarding[f"{user_id}:{workspace_id}"] = stored
        return stored

    async def begin_pairing(
        self,
        *,
        organisation_id: str,
        workspace_id: str,
        code_challenge: str,
    ) -> tuple[PairingSession, str]:
        device_code = secrets.token_urlsafe(32)
        pairing = PairingSession(
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            user_code="-".join((secrets.token_hex(2).upper(), secrets.token_hex(2).upper())),
            device_code_hash=hashlib.sha256(device_code.encode()).hexdigest(),
            code_challenge=code_challenge,
        )
        self.store.pairings[pairing.id] = pairing
        return pairing, device_code

    async def pairing_status(self, pairing_id: str) -> dict[str, Any]:
        pairing = self.store.pairings.get(pairing_id)
        if pairing is None:
            raise KeyError("pairing session not found")
        expired = pairing.expires_at <= utc_now()
        node = next(
            (
                item
                for item in self.store.nodes.values()
                if item.workspace_id == pairing.workspace_id
                and item.organisation_id == pairing.organisation_id
                and item.revoked_at is None
                and pairing.approved_by_user_id is not None
            ),
            None,
        )
        # Prefer node created after this pairing approval window.
        if pairing.approved_by_user_id:
            candidates = [
                item
                for item in self.store.nodes.values()
                if item.workspace_id == pairing.workspace_id
                and item.organisation_id == pairing.organisation_id
                and item.revoked_at is None
            ]
            node = candidates[-1] if candidates else None
        status = "pending"
        if expired and node is None:
            status = "expired"
        elif pairing.approved_by_user_id and node is None:
            status = "approved_waiting_for_node"
        elif node is not None:
            status = "paired"
        return {
            "pairing_id": pairing.id,
            "user_code": pairing.user_code,
            "status": status,
            "expires_at": pairing.expires_at.isoformat(),
            "approved": pairing.approved_by_user_id is not None,
            "node": None if node is None else node.model_dump(mode="json"),
        }

    async def cancel_pairing(self, pairing_id: str) -> None:
        pairing = self.store.pairings.get(pairing_id)
        if pairing is None:
            raise KeyError("pairing session not found")
        self.store.pairings[pairing_id] = pairing.model_copy(update={"expires_at": utc_now()})

    async def approve_pairing(self, pairing_id: str, *, user_id: str) -> PairingSession:
        pairing = self.store.pairings[pairing_id]
        if pairing.expires_at <= utc_now():
            raise PermissionError("pairing session expired")
        updated = pairing.model_copy(update={"approved_by_user_id": user_id})
        self.store.pairings[pairing_id] = updated
        return updated

    async def register_node(
        self,
        pairing_id: str,
        *,
        device_code: str,
        name: str,
        public_key: str,
        key_id: str,
        platform: str,
        version: str,
    ) -> NodeRegistration:
        pairing = self.store.pairings[pairing_id]
        if pairing.expires_at <= utc_now() or pairing.approved_by_user_id is None:
            raise PermissionError("pairing is not approved")
        supplied = hashlib.sha256(device_code.encode()).hexdigest()
        if not secrets.compare_digest(supplied, pairing.device_code_hash):
            raise PermissionError("invalid device code")
        node = NodeRegistration(
            organisation_id=pairing.organisation_id,
            workspace_id=pairing.workspace_id,
            name=name,
            public_key=public_key,
            key_id=key_id,
            platform=platform,
            version=version,
        )
        self.store.nodes[node.id] = node
        self._audit(
            organisation_id=node.organisation_id,
            actor_id=pairing.approved_by_user_id,
            action="node.register",
            target_type="node",
            target_id=node.id,
        )
        return node

    async def environment_diagnostics(self, *, node_id: str) -> dict[str, Any]:
        node = self.store.nodes.get(node_id)
        if node is None:
            raise KeyError("node not found")
        if node.revoked_at is not None:
            raise PermissionError("node revoked")
        return {
            "node_id": node.id,
            "node_online": True,
            "name": node.name,
            "operating_system": node.platform,
            "architecture": "unknown",
            "version": node.version,
            "available_package_managers": [],
            "supported_connector_hint": "Use connector readiness for install eligibility",
            "writable_install_locations": [],
            "path_state": "unknown",
            "required_restart": False,
        }

    async def grant_workspace(self, grant: WorkspaceGrant, *, actor_id: str) -> WorkspaceGrant:
        self.store.grants[grant.id] = grant
        self._audit(
            organisation_id=self.store.nodes[grant.node_id].organisation_id,
            actor_id=actor_id,
            action="workspace.grant",
            target_type="workspace_grant",
            target_id=grant.id,
        )
        return grant

    async def save_plan(self, plan: ActionPlan) -> ActionPlan:
        self.store.plans[plan.id] = plan
        return plan

    async def decide(self, decision: ApprovalDecision) -> ApprovalDecision:
        plan = self.store.plans[decision.plan_id]
        verify_approval(plan, decision)
        self.store.approvals[decision.id] = decision
        return decision

    async def create_remote_task(
        self,
        envelope: RemoteTaskEnvelope,
        *,
        signing_private_key: str,
    ) -> RemoteTaskEnvelope:
        node = self.store.nodes.get(envelope.node_id)
        if node is None or node.revoked_at is not None:
            raise PermissionError("node is unavailable or revoked")
        if node.organisation_id != envelope.organisation_id:
            raise PermissionError("cross-organisation task rejected")
        grants = [
            grant
            for grant in self.store.grants.values()
            if grant.node_id == node.id
            and grant.workspace_id == envelope.workspace_id
            and grant.revoked_at is None
        ]
        if not grants:
            raise PermissionError("workspace has not been granted to this node")
        self.nonces.consume(envelope.nonce, envelope.expires_at)
        signed = sign_envelope(envelope, signing_private_key)
        self.store.tasks[signed.id] = signed
        self._audit(
            organisation_id=signed.organisation_id,
            actor_id=signed.user_id,
            action="remote_task.create",
            target_type="remote_task",
            target_id=signed.id,
            metadata={"harness_id": signed.harness_id},
        )
        return signed

    async def revoke_node(self, node_id: str, *, actor_id: str) -> NodeRegistration:
        node = self.store.nodes[node_id]
        updated = node.model_copy(update={"revoked_at": utc_now()})
        self.store.nodes[node_id] = updated
        self._audit(
            organisation_id=node.organisation_id,
            actor_id=actor_id,
            action="node.revoke",
            target_type="node",
            target_id=node_id,
        )
        return updated

    def _audit(
        self,
        *,
        organisation_id: str,
        actor_id: str | None,
        action: str,
        target_type: str,
        target_id: str,
        outcome: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.store.audit.append(
            AuditEvent(
                organisation_id=organisation_id,
                actor_id=actor_id or "system",
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome=outcome,
                metadata=metadata or {},
            )
        )
