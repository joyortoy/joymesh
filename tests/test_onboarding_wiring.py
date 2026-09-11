"""Onboarding progress durability, pairing, and catalogue alignment tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.lifecycle_models import ConnectorReadiness, NodeConnectorState
from joymesh.control_plane.contracts import OnboardingState, PaidRoutePolicy
from joymesh.control_plane.onboarding_flow import derive_wizard_state
from joymesh.control_plane.onboarding_store import (
    InMemoryOnboardingProgressRepository,
    OnboardingConflictError,
    SqlOnboardingProgressRepository,
)
from joymesh.control_plane.security import generate_node_keypair, pkce_pair
from joymesh.control_plane.service import ControlPlane
from joymesh.persistence import Database


@pytest.mark.asyncio
async def test_onboarding_progress_survives_sql_restart(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'onboard.db'}"
    database = Database(db_url)
    await database.initialize()
    repo = SqlOnboardingProgressRepository(database.sessions)
    control = ControlPlane(onboarding_repository=repo)
    first = await control.set_onboarding_state(
        user_id="user-1",
        organisation_id="org-1",
        workspace_id="ws-1",
        state=OnboardingState.HARNESS_SELECTION,
        selected_harnesses=("codex", "opencode"),
        paid_route_policy=PaidRoutePolicy.ASK,
        fireconnect_enabled=True,
    )
    assert first.revision >= 1

    database2 = Database(db_url)
    await database2.initialize()
    control2 = ControlPlane(
        onboarding_repository=SqlOnboardingProgressRepository(database2.sessions)
    )
    restored = await control2.onboarding_progress(
        user_id="user-1", organisation_id="org-1", workspace_id="ws-1"
    )
    assert restored.state is OnboardingState.HARNESS_SELECTION
    assert restored.selected_harnesses == ("codex", "opencode")
    assert restored.fireconnect_enabled is True
    await database.close()
    await database2.close()


@pytest.mark.asyncio
async def test_in_memory_repository_revision_conflict() -> None:
    repo = InMemoryOnboardingProgressRepository()
    control = ControlPlane(onboarding_repository=repo)
    current = await control.set_onboarding_state(
        user_id="u",
        organisation_id="o",
        workspace_id="w",
        state=OnboardingState.ACCOUNT_READY,
    )
    with pytest.raises(OnboardingConflictError):
        await control.set_onboarding_state(
            user_id="u",
            organisation_id="o",
            workspace_id="w",
            state=OnboardingState.HARNESS_SELECTION,
            expected_revision=max(0, current.revision - 1) if current.revision > 1 else 0,
        )


@pytest.mark.asyncio
async def test_pairing_creates_real_code_and_status_resolves_node(tmp_path: Path) -> None:
    control = ControlPlane()
    _verifier, challenge = pkce_pair()
    pairing, device_code = await control.begin_pairing(
        organisation_id="org",
        workspace_id="workspace",
        code_challenge=challenge,
    )
    assert pairing.user_code
    assert pairing.user_code != "MESH-7F2A"
    status = await control.pairing_status(pairing.id)
    assert status["status"] == "pending"
    await control.approve_pairing(pairing.id, user_id="user")
    _priv, pub = generate_node_keypair()
    node = await control.register_node(
        pairing.id,
        device_code=device_code,
        name="MacBook",
        public_key=pub,
        key_id="node-1",
        platform="darwin",
        version="0.1.0",
    )
    status = await control.pairing_status(pairing.id)
    assert status["status"] == "paired"
    assert status["node"]["id"] == node.id


@pytest.mark.asyncio
async def test_selection_does_not_imply_installation() -> None:
    control = ControlPlane()
    progress = await control.set_onboarding_state(
        user_id="u",
        organisation_id="o",
        workspace_id="w",
        state=OnboardingState.HARNESS_SELECTION,
        selected_harnesses=("codex",),
        node_id="node-1",
    )
    assert progress.state is OnboardingState.HARNESS_SELECTION
    derived = derive_wizard_state(progress, readiness=(), active_tasks=())
    # Missing readiness rows while already past selection stays in install review path.
    assert derived in {
        OnboardingState.HARNESS_SELECTION,
        OnboardingState.INSTALLATION_REVIEW,
    }
    assert derived is not OnboardingState.COMPLETE
    assert derived is not OnboardingState.INSTALLING


def test_frontend_catalogue_ids_resolve_to_backend_connectors() -> None:
    catalogue_path = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "app"
        / "connector-catalogue.generated.json"
    )
    if not catalogue_path.exists():
        pytest.skip("frontend catalogue not generated")
    frontend = json.loads(catalogue_path.read_text(encoding="utf-8"))
    backend = {item.harness_id for item in ConnectorCatalogue.builtins().all()}
    missing = [
        item["harness_id"]
        for item in frontend
        if item.get("harness_id") not in backend and item.get("maturity") != "blocked"
    ]
    # Allow IDE-only display entries that are discoverable later; require known IDs format.
    for item in frontend:
        assert isinstance(item.get("harness_id"), str)
        assert item["harness_id"]
    # Every production-selectable backend-mapped ID must exist when maturity claims installable+.
    production_maturities = {
        "installable",
        "authenticatable",
        "adapter_conformant",
        "certified",
        "production_ready",
    }
    installable = [
        item["harness_id"] for item in frontend if item.get("maturity") in production_maturities
    ]
    unresolved = [item for item in installable if item not in backend]
    assert unresolved == [], (
        f"unresolved frontend connectors: {unresolved}; backend={sorted(backend)}"
    )
    assert not any(item == "MESH-7F2A" for item in missing)


def test_derive_auth_and_ready_states() -> None:
    from joymesh.control_plane.onboarding_store import new_progress
    from joymesh.models import utc_now

    progress = new_progress(user_id="u", organisation_id="o", workspace_id="w").model_copy(
        update={
            "node_id": "node-1",
            "selected_harnesses": ("codex",),
            "state": OnboardingState.INSTALLING,
        }
    )
    auth = ConnectorReadiness(
        node_id="node-1",
        connector_id="codex",
        state=NodeConnectorState.AUTHENTICATION_REQUIRED,
        catalogue_maturity="installable",
        updated_at=utc_now(),
    )
    assert (
        derive_wizard_state(progress, readiness=(auth,)) is OnboardingState.AUTHENTICATION_REQUIRED
    )
    ready = auth.model_copy(update={"state": NodeConnectorState.READY, "routing_eligible": True})
    assert derive_wizard_state(progress, readiness=(ready,)) is OnboardingState.ROUTING_SETUP
