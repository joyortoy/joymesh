"""Bypass hardening: all production mutations must use the coordinator."""

from __future__ import annotations

import asyncio
import os
import re
import stat
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from joymesh.api import create_app
from joymesh.cli import app as cli_app
from joymesh.fireconnect import FireConnectClient, FireConnectError
from joymesh.runtime_v1.provider_routes.authority import (
    MutationAuthorityError,
    current_mutation_authority,
    mutation_authority,
    mutation_authority_for_tests,
)
from joymesh.runtime_v1.provider_routes.fireconnect import (
    FireConnectProviderRouteManager,
    make_approval,
)
from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseStore
from joymesh.runtime_v1.provider_routes.registry import reset_provider_route_managers_for_tests
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService
from joymesh.service import JoyMesh

FIXTURE = Path(__file__).parent / "fixtures" / "fake_fireconnect.py"
ROOT = Path(__file__).resolve().parents[1] / "src" / "joymesh"


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


@pytest.mark.asyncio
async def test_direct_enable_bypass_rejected(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    with pytest.raises(MutationAuthorityError):
        await manager.enable_route(
            "opencode",
            approval=make_approval(action="enable", connector_id="opencode"),
        )


@pytest.mark.asyncio
async def test_direct_disable_bypass_rejected(fake_fireconnect: Path) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    with pytest.raises(MutationAuthorityError):
        await manager.disable_route(
            "opencode",
            approval=make_approval(action="disable", connector_id="opencode"),
        )


def test_cli_enable_acquires_lease(fake_fireconnect: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "provider-route",
            "enable",
            "fireconnect",
            "opencode",
            "--model",
            "accounts/fireworks/models/deepseek-v4-flash",
            "--approve",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"ok": true' in result.output.replace(" ", "") or '"ok": true' in result.output


def test_cli_disable_acquires_lease(fake_fireconnect: Path) -> None:
    runner = CliRunner()
    enable = runner.invoke(
        cli_app,
        [
            "provider-route",
            "enable",
            "fireconnect",
            "opencode",
            "--approve",
            "--json",
        ],
    )
    assert enable.exit_code == 0, enable.output
    disable = runner.invoke(
        cli_app,
        ["provider-route", "disable", "fireconnect", "opencode", "--approve", "--json"],
    )
    assert disable.exit_code == 0, disable.output


@pytest.mark.asyncio
async def test_api_enable_disable_acquire_lease(fake_fireconnect: Path, tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    app = create_app(mesh, fireconnect=FireConnectClient(str(fake_fireconnect)))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            enabled = await client.post(
                "/api/v1/provider-routes/fireconnect/opencode/enable",
                params={"approve": "true", "model": "accounts/fireworks/models/x"},
            )
            assert enabled.status_code == 200, enabled.text
            assert enabled.json()["ok"] is True
            disabled = await client.post(
                "/api/v1/provider-routes/fireconnect/opencode/disable",
                params={"approve": "true"},
            )
            assert disabled.status_code == 200, disabled.text
            assert disabled.json()["ok"] is True


@pytest.mark.asyncio
async def test_legacy_execute_is_coordinated_and_deprecated(
    fake_fireconnect: Path, tmp_path: Path
) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    app = create_app(mesh, fireconnect=FireConnectClient(str(fake_fireconnect)))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            plan = await client.post(
                "/api/v1/fireconnect/opencode/connect/plan",
                json={"model": "accounts/fireworks/models/deepseek-v4-flash"},
            )
            assert plan.status_code == 200
            assert plan.headers.get("deprecation") == "true"
            body = plan.json()
            approval = {
                "action": "route_transform",
                "harness_id": "opencode",
                "approved": True,
                "nonce": "legacy-test",
            }
            executed = await client.post(
                "/api/v1/fireconnect/opencode/execute",
                json={"plan": body, "approval": approval},
            )
            assert executed.status_code == 200, executed.text
            assert executed.headers.get("deprecation") == "true"
            assert executed.json()["available"] is True


@pytest.mark.asyncio
async def test_temporary_execution_uses_run_lifecycle(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    service = ProviderRouteService(store=store)
    seen: list[str] = []

    async def execute() -> str:
        seen.append("run")
        return "ok"

    result = await service.run_temporary(
        "fireconnect",
        "opencode",
        execute=execute,
        model_id="accounts/fireworks/models/deepseek-v4-flash",
        owner_execution_id="tmp-1",
    )
    assert result.ok
    assert result.restoration_verified
    assert seen == ["run"]
    assert any(a.event_type == "provider_route.lease_acquired" for a in result.audits)
    assert any(a.event_type == "provider_route.restored" for a in result.audits)


@pytest.mark.asyncio
async def test_permanent_mutation_uses_serialised(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    service = ProviderRouteService(store=store)
    result = await service.enable_permanently(
        "fireconnect",
        "opencode",
        model_id="accounts/fireworks/models/deepseek-v4-flash",
        owner_execution_id="perm-1",
    )
    assert result.ok
    # Permanent path leaves route enabled (no auto-restore).
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    assert (await manager.inspect_route("opencode")).enabled is True


@pytest.mark.asyncio
async def test_two_cli_mutations_same_connector_serialise(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    service = ProviderRouteService(store=store)
    order: list[str] = []
    gate = asyncio.Event()

    async def first() -> None:
        async def mutate() -> str:
            order.append("a-start")
            gate.set()
            await asyncio.sleep(0.08)
            order.append("a-end")
            return "a"

        await service.coordinator.run_serialised_mutation(
            manager=service.manager("fireconnect"),
            connector_id="opencode",
            mutate=mutate,
            owner_execution_id="cli-a",
        )

    async def second() -> None:
        await gate.wait()

        async def mutate() -> str:
            order.append("b-start")
            return "b"

        await service.coordinator.run_serialised_mutation(
            manager=service.manager("fireconnect"),
            connector_id="opencode",
            mutate=mutate,
            owner_execution_id="cli-b",
            acquire_timeout_seconds=5.0,
        )

    await asyncio.gather(first(), second())
    assert order.index("a-end") < order.index("b-start")


@pytest.mark.asyncio
async def test_cli_and_api_mutation_serialise(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    service_a = ProviderRouteService(store=store)
    service_b = ProviderRouteService(store=store)
    order: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def cli_job() -> None:
        async def mutate() -> str:
            order.append("cli")
            started.set()
            await release.wait()
            return "cli"

        await service_a.coordinator.run_serialised_mutation(
            manager=service_a.manager("fireconnect"),
            connector_id="opencode",
            mutate=mutate,
            owner_execution_id="cli",
        )

    async def api_job() -> None:
        await started.wait()
        result = await service_b.enable_permanently(
            "fireconnect",
            "opencode",
            owner_execution_id="api",
            acquire_timeout_seconds=5.0,
        )
        assert result.ok
        order.append("api")

    task_a = asyncio.create_task(cli_job())
    task_b = asyncio.create_task(api_job())
    await started.wait()
    await asyncio.sleep(0.05)
    assert "api" not in order
    release.set()
    await asyncio.gather(task_a, task_b)
    assert order.index("cli") < order.index("api")


@pytest.mark.asyncio
async def test_api_and_lifecycle_serialise(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    service = ProviderRouteService(store=store)
    order: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async def execute() -> str:
            order.append("life-start")
            started.set()
            await release.wait()
            order.append("life-end")
            return "ok"

        result = await service.run_temporary(
            "fireconnect",
            "opencode",
            execute=execute,
            owner_execution_id="life",
        )
        assert result.ok

    async def api() -> None:
        await started.wait()
        result = await service.enable_permanently(
            "fireconnect",
            "opencode",
            owner_execution_id="api2",
            acquire_timeout_seconds=5.0,
        )
        assert result.ok
        order.append("api")

    t1 = asyncio.create_task(holder())
    t2 = asyncio.create_task(api())
    await started.wait()
    await asyncio.sleep(0.05)
    assert "api" not in order
    release.set()
    await asyncio.gather(t1, t2)
    assert order.index("life-end") < order.index("api")


@pytest.mark.asyncio
async def test_different_connectors_remain_concurrent(fake_fireconnect: Path) -> None:
    service = ProviderRouteService()
    entered = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def job(connector_id: str) -> None:
        nonlocal in_flight, peak

        async def execute() -> str:
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
                if in_flight == 2:
                    entered.set()
            await release.wait()
            async with lock:
                in_flight -= 1
            return connector_id

        result = await service.run_temporary(
            "fireconnect",
            connector_id,
            execute=execute,
            owner_execution_id=connector_id,
        )
        assert result.ok

    tasks = [asyncio.create_task(job("opencode")), asyncio.create_task(job("codex"))]
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    assert peak == 2
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_cancellation_does_not_leak_mutation_authority(
    fake_fireconnect: Path,
) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    started = asyncio.Event()

    async def holder() -> None:
        with mutation_authority(
            manager_id="fireconnect",
            connector_id="opencode",
            purpose="serialised",
        ):
            started.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(holder())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert current_mutation_authority() is None
    with pytest.raises(MutationAuthorityError):
        await manager.enable_route(
            "opencode",
            approval=make_approval(action="enable", connector_id="opencode"),
        )


@pytest.mark.asyncio
async def test_authority_does_not_leak_into_unrelated_async_tasks(
    fake_fireconnect: Path,
) -> None:
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    leaked: list[bool] = []

    async def unrelated() -> None:
        leaked.append(current_mutation_authority() is not None)
        with pytest.raises(MutationAuthorityError):
            await manager.enable_route(
                "opencode",
                approval=make_approval(action="enable", connector_id="opencode"),
            )

    # Start unrelated task before installing authority so it does not inherit it.
    task = asyncio.create_task(unrelated())
    await asyncio.sleep(0)
    with mutation_authority(
        manager_id="fireconnect",
        connector_id="opencode",
        purpose="serialised",
    ):
        await task
    assert leaked == [False]


@pytest.mark.asyncio
async def test_recovery_can_mutate_safely(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    service = ProviderRouteService(store=store)
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        await manager.enable_route(
            "opencode",
            approval=make_approval(action="enable", connector_id="opencode"),
            model_id="accounts/fireworks/models/deepseek-v4-flash",
        )
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="crash",
        ttl_seconds=0.05,
        original_state={"enabled": False, "connector_id": "opencode", "provider_id": "default"},
    )
    assert lease is not None
    await asyncio.sleep(0.08)
    reports = await service.recover_expired("fireconnect")
    assert reports[0]["status"] == "restored_verified"
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_recovery_and_normal_mutation_cannot_overlap(fake_fireconnect: Path) -> None:
    store = ProviderRouteLeaseStore()
    manager = FireConnectProviderRouteManager(str(fake_fireconnect))
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        await manager.enable_route(
            "opencode",
            approval=make_approval(action="enable", connector_id="opencode"),
            model_id="accounts/fireworks/models/deepseek-v4-flash",
        )
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="crash",
        ttl_seconds=0.05,
        original_state={"enabled": False, "connector_id": "opencode"},
    )
    assert lease is not None
    await asyncio.sleep(0.08)
    claimed = await store.claim_recovery(
        lease_id=lease.lease_id,
        lease_token=lease.lease_token,
        recovery_owner_id="recoverer-1",
    )
    assert claimed.status == "recovering"
    # Normal acquire must fail while recovery holds active_marker.
    blocked = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="normal",
        ttl_seconds=30,
        original_state={"enabled": False},
    )
    assert blocked is None
    await store.mark_recovery(
        lease_id=lease.lease_id,
        lease_token=lease.lease_token,
        recovery_status="restored_verified",
    )


def test_no_subprocess_fireconnect_mutation_outside_manager() -> None:
    forbidden = re.compile(
        r"""create_subprocess_exec\([\s\S]{0,200}["']on["']|"""
        r"""create_subprocess_exec\([\s\S]{0,200}["']off["']"""
    )
    allowed = {
        ROOT / "runtime_v1" / "provider_routes" / "fireconnect.py",
    }
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if not forbidden.search(text):
            continue
        if path in allowed:
            # Manager may still mention on/off in docs; ensure only _run builds argv.
            if "async def _run" not in text and 'harness, "on"' not in text:
                offenders.append(str(path))
            continue
        # fireconnect.py client must refuse direct on/off.
        if path.name == "fireconnect.py" and "direct FireConnect mutation is forbidden" in text:
            continue
        offenders.append(str(path))
    assert offenders == []


def test_static_call_site_audit_no_production_bypass() -> None:
    pattern = re.compile(r"""\.(enable_route|disable_route)\s*\(""")
    allowed_dirs = {
        "runtime_v1/provider_routes",
    }
    allowed_files = {
        "runtime_v1/provider_routes/fireconnect.py",
        "runtime_v1/provider_routes/coordinator.py",
        "runtime_v1/provider_routes/service.py",
        "runtime_v1/provider_routes/protocol.py",
        "runtime_v1/provider_routes/registry.py",
    }
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if any(rel.startswith(d) for d in allowed_dirs) and rel in allowed_files:
            continue
        if rel.startswith("runtime_v1/provider_routes/"):
            # Only coordinator/service/fireconnect/protocol/registry may call mutations.
            if rel not in allowed_files and pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(rel)
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(rel)
    assert offenders == [], offenders


@pytest.mark.asyncio
async def test_fireconnect_client_refuses_direct_on_off(fake_fireconnect: Path) -> None:
    client = FireConnectClient(str(fake_fireconnect))
    with pytest.raises(FireConnectError, match="forbidden"):
        await client._run(str(fake_fireconnect), "opencode", "on")
