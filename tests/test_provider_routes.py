"""Provider-route manager tests — FireConnect is not a ConnectorRuntime."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from joymesh.runtime_v1.connectors import builtin_connectors, reset_builtin_connectors_for_tests
from joymesh.runtime_v1.provider_routes import (
    builtin_provider_route_managers,
    get_provider_route_manager,
    mutation_authority_for_tests,
    native_route_for,
    reset_provider_route_managers_for_tests,
    select_provider_route,
)
from joymesh.runtime_v1.provider_routes.authority import MutationAuthorityError
from joymesh.runtime_v1.provider_routes.fireconnect import (
    FireConnectManagerError,
    FireConnectProviderRouteManager,
    make_approval,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_fireconnect.py"


@pytest.fixture
def fake_fireconnect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "fireconnect"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "fc-state.json"
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "success")
    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_STATE", str(state))
    reset_provider_route_managers_for_tests()
    return target


def test_fireconnect_not_in_builtin_connectors() -> None:
    reset_builtin_connectors_for_tests()
    connectors = builtin_connectors()
    assert "fireconnect" not in connectors
    assert set(connectors) >= {"cursor", "codex", "opencode", "claude"}


def test_fireconnect_registered_as_route_manager_only() -> None:
    reset_provider_route_managers_for_tests()
    managers = builtin_provider_route_managers()
    assert set(managers) == {"fireconnect"}
    assert managers["fireconnect"].manager_id == "fireconnect"
    assert "fireconnect" not in builtin_connectors()


def test_no_fireconnect_branches_in_shared_runtime() -> None:
    root = Path(__file__).parents[1] / "src" / "joymesh"
    forbidden = re.compile(
        r"""connector_id\s*(==|!=)\s*['\"]fireconnect['\"]"""
        r"""|if\s+.*['\"]fireconnect['\"].*connector"""
    )
    shared = [
        root / "runtime_v1" / "scheduler.py",
        root / "runtime_v1" / "connectors" / "live_test.py",
        root / "connectors" / "node_runner.py",
        root / "runtime_v1" / "connectors" / "__init__.py",
    ]
    for path in shared:
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), path


@pytest.mark.asyncio
async def test_discovery_success(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    result = await manager.discover()
    assert result.installed is True
    assert result.usable is True
    assert result.version == "0.9.0"
    assert "opencode" in result.supported_harnesses
    assert result.details.get("role") == "provider_configuration_manager"


@pytest.mark.asyncio
async def test_discovery_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    manager = FireConnectProviderRouteManager()
    # Force no fallback by pointing at missing path.
    manager = FireConnectProviderRouteManager("/tmp/definitely-missing-fireconnect-xyz")
    result = await manager.discover()
    assert result.installed is False
    assert result.reason_code == "manager_not_installed"


@pytest.mark.asyncio
async def test_discovery_broken(fake_fireconnect: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "broken")
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    result = await manager.discover()
    assert result.usable is False
    assert result.reason_code == "broken_executable"


@pytest.mark.asyncio
async def test_auth_states(fake_fireconnect: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    auth = await manager.inspect_auth()
    assert auth.status == "authenticated"
    assert auth.signed_in is True
    blob = json.dumps(auth.as_dict())
    assert "user@example.test" not in blob
    assert "acct_secret" not in blob

    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "unauthenticated")
    auth = await manager.inspect_auth()
    assert auth.status == "unauthenticated"

    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "malformed_status")
    auth = await manager.inspect_auth()
    assert auth.status == "misconfigured"


@pytest.mark.asyncio
async def test_route_inspection_native_and_fireworks(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    routes = await manager.list_routes("opencode")
    providers = {item.provider_id for item in routes}
    assert providers == {"native", "fireworks"}
    fireworks = next(item for item in routes if item.provider_id == "fireworks")
    assert fireworks.enabled is False
    assert fireworks.manager_id == "fireconnect"
    assert fireworks.connector_id == "opencode"


@pytest.mark.asyncio
async def test_unsupported_connector(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    route = await manager.inspect_route("grok")
    assert route.available is False
    assert route.reason_code == "route_not_supported"


@pytest.mark.asyncio
async def test_enable_requires_approval(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    bad = make_approval(action="enable", connector_id="opencode")
    bad = bad.__class__(
        approved=False,
        action="enable",
        manager_id="fireconnect",
        connector_id="opencode",
        nonce="x",
    )
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        with pytest.raises(FireConnectManagerError, match="approval"):
            await manager.enable_route("opencode", approval=bad)


@pytest.mark.asyncio
async def test_enable_disable_rollback(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    model = "accounts/fireworks/models/deepseek-v4-flash"
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        enabled = await manager.enable_route(
            "opencode",
            approval=make_approval(action="enable", connector_id="opencode", model_id=model),
            model_id=model,
        )
    assert enabled.ok is True
    assert enabled.route is not None
    assert enabled.route.enabled is True
    assert enabled.route.model_id == model
    assert enabled.previous_snapshot["enabled"] is False

    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        disabled = await manager.disable_route(
            "opencode",
            approval=make_approval(action="disable", connector_id="opencode"),
        )
    assert disabled.ok is True
    assert disabled.restored is True
    assert disabled.route is not None
    assert disabled.route.enabled is False


@pytest.mark.asyncio
async def test_enable_idempotent(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    approval = make_approval(action="enable", connector_id="codex", model_id="m1")
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="codex"):
        first = await manager.enable_route("codex", approval=approval, model_id="m1")
        second = await manager.enable_route("codex", approval=approval, model_id="m1")
    assert first.ok and second.ok
    assert second.route is not None and second.route.enabled is True


@pytest.mark.asyncio
async def test_direct_enable_bypass_rejected(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    with pytest.raises(MutationAuthorityError, match="coordinator"):
        await manager.enable_route(
            "opencode",
            approval=make_approval(action="enable", connector_id="opencode"),
        )


@pytest.mark.asyncio
async def test_direct_disable_bypass_rejected(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    with pytest.raises(MutationAuthorityError, match="coordinator"):
        await manager.disable_route(
            "opencode",
            approval=make_approval(action="disable", connector_id="opencode"),
        )


def test_selection_prefers_native_by_default() -> None:
    routes = (
        native_route_for("opencode"),
        native_route_for("opencode").__class__(
            route_id="opencode:fireworks",
            display_name="fw",
            manager_id="fireconnect",
            connector_id="opencode",
            provider_id="fireworks",
            model_id="accounts/fireworks/models/x",
            enabled=True,
            available=True,
            authenticated=True,
            configuration_status="valid",
            supports_enable=True,
            supports_disable=True,
        ),
    )
    result = select_provider_route(connector_id="opencode", routes=routes)
    assert result.selected_provider_route is not None
    assert result.selected_provider_route.provider_id == "native"


def test_selection_respects_preferred_fireworks() -> None:
    routes = (
        native_route_for("opencode"),
        native_route_for("opencode").__class__(
            route_id="opencode:fireworks",
            display_name="fw",
            manager_id="fireconnect",
            connector_id="opencode",
            provider_id="fireworks",
            model_id="accounts/fireworks/models/x",
            enabled=True,
            available=True,
            authenticated=True,
            configuration_status="valid",
            supports_enable=True,
            supports_disable=True,
        ),
    )
    result = select_provider_route(
        connector_id="opencode",
        routes=routes,
        preferred_providers=("fireworks",),
    )
    assert result.selected_provider_route is not None
    assert result.selected_provider_route.provider_id == "fireworks"
    assert result.selected_model == "accounts/fireworks/models/x"


def test_selection_excludes_disabled_fireworks() -> None:
    routes = (
        native_route_for("opencode"),
        native_route_for("opencode").__class__(
            route_id="opencode:fireworks",
            display_name="fw",
            manager_id="fireconnect",
            connector_id="opencode",
            provider_id="fireworks",
            model_id=None,
            enabled=False,
            available=True,
            authenticated=True,
            configuration_status="native_default",
            supports_enable=True,
            supports_disable=True,
        ),
    )
    result = select_provider_route(
        connector_id="opencode",
        routes=routes,
        preferred_providers=("fireworks",),
    )
    assert result.selected_provider_route is not None
    assert result.selected_provider_route.provider_id == "native"


def test_selection_no_silent_fallback_when_required_unavailable() -> None:
    routes = (
        native_route_for("opencode"),
        native_route_for("opencode").__class__(
            route_id="opencode:fireworks",
            display_name="fw",
            manager_id="fireconnect",
            connector_id="opencode",
            provider_id="fireworks",
            model_id=None,
            enabled=False,
            available=False,
            authenticated=False,
            configuration_status="unavailable",
            reason_code="authentication_required",
            supports_enable=True,
            supports_disable=True,
        ),
    )
    result = select_provider_route(
        connector_id="opencode",
        routes=routes,
        required_provider="fireworks",
    )
    assert result.selected_provider_route is None
    assert result.provider_selection_reason == "no_eligible_provider_route"


def test_redact_diagnostics(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    text = manager.redact_diagnostics(
        "FIREWORKS_API_KEY=fw_secret_value_here Bearer abcdefghijklmnop"
    )
    assert "fw_secret" not in text
    assert "abcdefghijklmnop" not in text
    assert "[REDACTED]" in text


@pytest.mark.asyncio
async def test_registry_get_manager(fake_fireconnect: Path) -> None:
    # Ensure PATH fake is picked up by registry default instance after reset.
    reset_provider_route_managers_for_tests()
    manager = get_provider_route_manager("fireconnect")
    # Default manager resolves via PATH which includes fake.
    discovery = await manager.discover()
    assert discovery.installed is True
