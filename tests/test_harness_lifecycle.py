from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.fireconnect import FireConnectClient, FireConnectError
from joymesh.harnesses.catalogue import builtin_catalogue, render_capability_matrix
from joymesh.harnesses.contracts import (
    ApprovalToken,
    BillingMode,
    CertificationState,
    LifecycleAction,
)
from joymesh.harnesses.discovery import DiscoveryPolicy, HarnessDiscovery
from joymesh.harnesses.lifecycle import LifecycleApprovalError, LifecyclePlanError
from joymesh.harnesses.routing_transforms import (
    compatible_router_transform,
    fireworks_transform,
)
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh


def test_catalogue_covers_required_harnesses_and_aliases() -> None:
    registry = AdapterRegistry()
    ids = {definition.id for definition in registry.definitions()}

    assert {
        "codex",
        "opencode",
        "claude-code",
        "gemini-cli",
        "github-copilot",
        "aider",
        "goose",
        "pi",
        "continue",
        "amazon-q",
        "qwen-code",
        "cline",
        "roo-code",
    } <= ids
    assert registry.resolve_id("claude") == "claude-code"
    assert registry.resolve_id("gemini") == "gemini-cli"
    assert registry.definition("amazon-q").unsupported_reason == (
        "official_general_chat_headless_contract_not_documented"
    )


def test_documented_capability_matrix_is_generated_from_catalogue() -> None:
    path = Path(__file__).parents[1] / "docs" / "harness-capability-matrix.md"
    assert path.read_text(encoding="utf-8") == render_capability_matrix()


async def test_discovery_is_deterministic_and_version_probe_is_opt_in(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "executed"
    binary = tmp_path / "codex"
    binary.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "print('codex 9.9.9')\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    discovery = HarnessDiscovery(builtin_catalogue())
    environment = {"PATH": str(tmp_path)}

    passive = await discovery.discover("codex", environment=environment)
    assert passive.installations[0].version is None
    assert not marker.exists()

    active = await discovery.discover(
        "codex",
        environment=environment,
        policy=DiscoveryPolicy(execute_version_commands=True),
    )
    assert active.installations[0].version == "codex 9.9.9"
    assert marker.exists()
    assert passive.installations[0].executable == active.installations[0].executable


async def test_lifecycle_plan_requires_matching_explicit_approval(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    try:
        plan = mesh.plan_install("gemini-cli")
        assert plan.argv == ("npm", "install", "--global", "@google/gemini-cli")
        assert plan.dry_run
        wrong = ApprovalToken(
            action=LifecycleAction.UPGRADE,
            harness_id="gemini-cli",
            approved=True,
            nonce="wrong-action",
        )
        with pytest.raises(LifecycleApprovalError):
            await mesh.execute_lifecycle_plan(plan, approval=wrong)

        approved = wrong.model_copy(
            update={"action": LifecycleAction.INSTALL, "nonce": "approved-install"}
        )
        tampered = plan.model_copy(update={"argv": ("python", "-c", "print('unsafe')")})
        with pytest.raises(LifecyclePlanError, match="official catalogue"):
            await mesh.execute_lifecycle_plan(tampered, approval=approved)
        result = await mesh.execute_lifecycle_plan(plan, approval=approved)
        assert result.return_code == 0
        assert result.stdout == ""
        login = mesh.plan_login("codex")
        assert login.argv == ("codex", "login")
    finally:
        await mesh.close()


async def test_certification_evidence_is_version_aware(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'cert.db'}")
    await mesh.initialize()
    try:
        evidence = await mesh.certification.record(
            harness_id="codex",
            binary_version="codex 1.2.3",
            executable="/tmp/codex",
            checks={"launch_specification": True, "secret_redaction": True},
        )
        persisted = await mesh.certifications(harness_id="codex")
        assert evidence.state is CertificationState.BINARY_CERTIFIED
        assert len(persisted) == 1
        assert persisted[0].id == evidence.id
        assert persisted[0].binary_version == "codex 1.2.3"
    finally:
        await mesh.close()


async def test_harness_lifecycle_api_uses_same_service(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            catalogue = await client.get("/api/v1/harnesses/catalogue")
            assert catalogue.status_code == 200
            assert any(item["id"] == "claude-code" for item in catalogue.json())

            discovered = await client.post(
                "/api/v1/harnesses/discovery",
                json={"harness_id": "codex", "probe_versions": False},
            )
            assert discovered.status_code == 200
            assert discovered.json()[0]["harness_id"] == "codex"

            inspected = await client.get("/api/v1/harnesses/amazon-q")
            assert inspected.status_code == 200
            assert inspected.json()["definition"]["maturity"] == "discovery_only"

            planned = await client.post("/api/v1/harnesses/gemini/install/plan")
            assert planned.status_code == 200
            assert json.loads(planned.text)["dry_run"] is True

    await mesh.close()


async def test_fireconnect_transform_is_read_only_until_approved(tmp_path: Path) -> None:
    binary = tmp_path / "fireconnect"
    marker = tmp_path / "changed"
    state = tmp_path / "fc-state.json"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        f"state_path = Path({str(state)!r})\n"
        "state = json.loads(state_path.read_text()) if state_path.exists() else "
        "{'enabled': False, 'model': None}\n"
        "if sys.argv[1:3] == ['status', '--json']:\n"
        " print(json.dumps({'auth': {'signedIn': True}, 'environment': "
        "{'cliVersion': '1.0'}, 'perHarness': [{'id': 'codex', "
        "'enabled': state['enabled']}]}))\n"
        "elif len(sys.argv) >= 3 and sys.argv[2:4] == ['status', '--json']:\n"
        " print(json.dumps({\n"
        "  'harness': sys.argv[1],\n"
        "  'provider': 'fireworks' if state['enabled'] else 'default',\n"
        "  'hasAuthToken': state['enabled'],\n"
        "  'current': {'main': state['model']},\n"
        " }))\n"
        "elif len(sys.argv) >= 3 and sys.argv[2] == 'on':\n"
        " model = sys.argv[sys.argv.index('--model')+1] if '--model' in sys.argv else 'x'\n"
        " state_path.write_text(json.dumps({'enabled': True, 'model': model}))\n"
        " marker.write_text('changed')\n"
        " print('enabled')\n"
        "elif len(sys.argv) >= 3 and sys.argv[2] == 'off':\n"
        " state_path.write_text(json.dumps({'enabled': False, 'model': None}))\n"
        " print('disabled')\n"
        "else:\n"
        " print('unknown', file=sys.stderr); raise SystemExit(2)\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    client = FireConnectClient(str(binary))
    plan = client.plan_connect("codex", "accounts/fw/models/test")
    assert not marker.exists()
    wrong = ApprovalToken(
        action=LifecycleAction.INSTALL,
        harness_id="codex",
        approved=True,
        nonce="wrong-fireconnect",
    )
    with pytest.raises(FireConnectError, match="approval"):
        await client.execute_plan(plan, wrong)
    assert not marker.exists()

    approved = wrong.model_copy(
        update={"action": LifecycleAction.ROUTE_TRANSFORM, "nonce": "route-approved"}
    )
    status = await client.execute_plan(plan, approved)
    assert marker.exists()
    assert status.available
    assert status.targets[0].model == "accounts/fw/models/test"


def test_external_router_transforms_preserve_billing_boundaries() -> None:
    fireworks = fireworks_transform("accounts/acme/routers/coding")
    assert fireworks.requires_approval
    assert fireworks.funding.billing_mode is BillingMode.PAID_API

    included = compatible_router_transform(
        transform_id="internal",
        endpoint="https://router.example.test/v1",
        provider="internal",
        model="coding",
        billing_mode=BillingMode.INCLUDED_ALLOWANCE,
    )
    assert not included.requires_approval
    with pytest.raises(ValueError, match="HTTPS"):
        compatible_router_transform(
            transform_id="unsafe",
            endpoint="http://router.example.test",
            provider="unsafe",
            model="coding",
        )
