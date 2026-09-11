"""Deterministic concurrency / lease tests for provider-route mutations."""

from __future__ import annotations

import asyncio
import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from joymesh.models import utc_now
from joymesh.persistence import Database
from joymesh.runtime_v1.provider_routes.authority import mutation_authority_for_tests
from joymesh.runtime_v1.provider_routes.coordinator import ProviderRouteMutationCoordinator
from joymesh.runtime_v1.provider_routes.fireconnect import (
    FireConnectProviderRouteManager,
    make_approval,
)
from joymesh.runtime_v1.provider_routes.lease_store import (
    ProviderRouteLeaseError,
    ProviderRouteLeaseStore,
    sanitise_route_state,
)
from joymesh.runtime_v1.provider_routes.registry import reset_provider_route_managers_for_tests

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


def _manager(fake: Path) -> FireConnectProviderRouteManager:
    return FireConnectProviderRouteManager(str(fake))


def _approval(connector_id: str = "opencode", model: str | None = None):
    return make_approval(action="enable", connector_id=connector_id, model_id=model)


@pytest.mark.asyncio
async def test_same_connector_same_route_serialised(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    store = ProviderRouteLeaseStore()
    coordinator = ProviderRouteMutationCoordinator(store)
    order: list[str] = []
    gate = asyncio.Event()

    async def job(name: str) -> None:
        async def execute() -> str:
            order.append(f"{name}:start")
            if name == "A":
                gate.set()
                await asyncio.sleep(0.08)
            else:
                await gate.wait()
            order.append(f"{name}:end")
            return name

        result = await coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(),
            execute=execute,
            owner_execution_id=name,
            acquire_timeout_seconds=5.0,
            lease_ttl_seconds=30.0,
        )
        assert result.ok
        assert result.restoration_verified
        order.append(f"{name}:done")

    await asyncio.gather(job("A"), job("B"))
    assert order.index("A:end") < order.index("B:start")
    route = await manager.inspect_route("opencode")
    assert route.enabled is False


@pytest.mark.asyncio
async def test_same_connector_different_routes_serialised(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())
    active: list[str] = []
    max_parallel = 0
    outcomes: list[str] = []

    async def job(name: str, model: str) -> None:
        nonlocal max_parallel

        async def execute() -> str:
            active.append(name)
            nonlocal_max = len(active)
            # capture peak under mutual exclusion of the coordinator lock
            outcomes.append(f"{name}:enter:{nonlocal_max}")
            await asyncio.sleep(0.05)
            active.remove(name)
            return name

        result = await coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(model=model),
            execute=execute,
            model_id=model,
            owner_execution_id=name,
            acquire_timeout_seconds=5.0,
        )
        outcomes.append(f"{name}:ok={result.ok}:{result.reason_code}")
        assert result.ok, outcomes

    await asyncio.gather(
        job("job_a", "accounts/fireworks/models/deepseek-v4-flash"),
        job("job_b", "accounts/fireworks/models/glm-fast-latest"),
    )
    enters = [item for item in outcomes if ":enter:" in item]
    peaks = [int(item.split(":")[-1]) for item in enters]
    assert peaks and max(peaks) == 1, outcomes


@pytest.mark.asyncio
async def test_different_connectors_may_run_concurrently(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())
    entered = asyncio.Event()
    release = asyncio.Event()
    in_execute = 0
    max_parallel = 0
    counter = asyncio.Lock()

    async def job(connector_id: str) -> None:
        nonlocal max_parallel, in_execute

        async def execute() -> str:
            nonlocal max_parallel, in_execute
            async with counter:
                in_execute += 1
                max_parallel = max(max_parallel, in_execute)
                if in_execute == 2:
                    entered.set()
            await asyncio.wait_for(release.wait(), timeout=10.0)
            async with counter:
                in_execute -= 1
            return connector_id

        result = await coordinator.run_lifecycle(
            manager=manager,
            connector_id=connector_id,
            approval=_approval(connector_id),
            execute=execute,
            owner_execution_id=connector_id,
            acquire_timeout_seconds=15.0,
        )
        assert result.ok, (connector_id, result.reason_code, result.message)

    tasks = [
        asyncio.create_task(job("opencode"), name="opencode"),
        asyncio.create_task(job("codex"), name="codex"),
    ]
    try:
        await asyncio.wait_for(entered.wait(), timeout=20.0)
    except TimeoutError as exc:
        errors = []
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception() is not None:
                errors.append(f"{task.get_name()}:{task.exception()!r}")
        raise AssertionError(f"connectors did not overlap; errors={errors}") from exc
    assert max_parallel == 2
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_waiting_job_cancellation(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())
    holder_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async def execute() -> None:
            holder_started.set()
            await release_holder.wait()

        await coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(),
            execute=execute,
            owner_execution_id="holder",
            acquire_timeout_seconds=5.0,
        )

    async def waiter() -> None:
        await holder_started.wait()

        async def execute() -> None:
            raise AssertionError("waiter must not execute")

        await coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(),
            execute=execute,
            owner_execution_id="waiter",
            acquire_timeout_seconds=5.0,
        )

    holder_task = asyncio.create_task(holder())
    waiter_task = asyncio.create_task(waiter())
    await holder_started.wait()
    await asyncio.sleep(0.05)
    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task
    release_holder.set()
    await holder_task
    route = await manager.inspect_route("opencode")
    assert route.enabled is False


@pytest.mark.asyncio
async def test_lock_acquisition_timeout(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    store = ProviderRouteLeaseStore()
    coordinator = ProviderRouteMutationCoordinator(store)
    holder_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async def execute() -> None:
            holder_started.set()
            await release_holder.wait()

        await coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(),
            execute=execute,
            owner_execution_id="holder",
            acquire_timeout_seconds=5.0,
            lease_ttl_seconds=30.0,
        )

    holder_task = asyncio.create_task(holder())
    await holder_started.wait()

    async def execute() -> None:
        raise AssertionError("must not run")

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=execute,
        owner_execution_id="waiter",
        acquire_timeout_seconds=0.15,
        poll_interval_seconds=0.02,
    )
    assert result.ok is False
    assert result.reason_code == "lock_acquisition_timeout"
    release_holder.set()
    await holder_task


@pytest.mark.asyncio
async def test_holder_execution_failure(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())

    async def execute() -> None:
        raise RuntimeError("boom")

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=execute,
        owner_execution_id="fail",
    )
    assert result.ok is False
    assert result.reason_code == "holder_execution_failed"
    assert result.restoration_verified is True
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_holder_timeout(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())

    async def execute() -> None:
        await asyncio.sleep(10)

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=lambda: asyncio.wait_for(execute(), timeout=0.05),
        owner_execution_id="timeout",
    )
    assert result.ok is False
    assert result.reason_code == "holder_timeout"
    assert result.restoration_verified is True
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_holder_cancellation(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())
    started = asyncio.Event()

    async def execute() -> None:
        started.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(
        coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(),
            execute=execute,
            owner_execution_id="cancel",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_provider_enable_failure(
    fake_fireconnect: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "enable_fail")
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=lambda: asyncio.sleep(0),
        owner_execution_id="enable-fail",
    )
    assert result.ok is False
    assert result.reason_code in {"mutation_failed", "provider_enable_failed"}
    assert result.restoration_verified is True


@pytest.mark.asyncio
async def test_provider_status_verification_failure(
    fake_fireconnect: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "verify_fail")
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=lambda: asyncio.sleep(0),
        owner_execution_id="verify-fail",
    )
    assert result.ok is False
    assert result.reason_code in {
        "configuration_invalid",
        "provider_status_verification_failed",
        "provider_enable_failed",
    }
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_restoration_failure(fake_fireconnect: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())

    async def execute() -> str:
        monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "restore_fail")
        return "ok"

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=execute,
        owner_execution_id="restore-fail",
    )
    assert result.ok is False
    assert result.reason_code == "restoration_failed"
    assert result.restoration_verified is False


@pytest.mark.asyncio
async def test_exact_original_state_restoration(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    model_a = "accounts/fireworks/models/deepseek-v4-flash"
    model_b = "accounts/fireworks/models/glm-fast-latest"
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        enable = await manager.enable_route(
            "opencode",
            approval=_approval(model=model_a),
            model_id=model_a,
        )
    assert enable.ok
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())
    observed: list[str | None] = []

    async def execute() -> str:
        route = await manager.inspect_route("opencode")
        observed.append(route.model_id)
        return "ok"

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(model=model_b),
        execute=execute,
        model_id=model_b,
        owner_execution_id="exact",
    )
    assert result.ok
    assert result.original_state.get("enabled") is True
    assert result.original_state.get("model_id")
    restored = await manager.inspect_route("opencode")
    assert restored.enabled is True
    assert restored.model_id == model_a or (restored.model_id or "").endswith("deepseek-v4-flash")
    assert any(item and "glm-fast-latest" in item for item in observed)


@pytest.mark.asyncio
async def test_original_route_already_enabled(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    model = "accounts/fireworks/models/deepseek-v4-flash"
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        assert (
            await manager.enable_route("opencode", approval=_approval(model=model), model_id=model)
        ).ok
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())

    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(model=model),
        execute=lambda: asyncio.sleep(0, result="ok"),
        model_id=model,
        owner_execution_id="already",
    )
    assert result.ok
    assert any(a.event_type == "provider_route.already_enabled" for a in result.audits)
    restored = await manager.inspect_route("opencode")
    assert restored.enabled is True


@pytest.mark.asyncio
async def test_duplicate_release_and_stale_token() -> None:
    store = ProviderRouteLeaseStore()
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="owner",
        ttl_seconds=30,
        original_state={"enabled": False},
    )
    assert lease is not None
    await store.release(
        lease_id=lease.lease_id,
        lease_token=lease.lease_token,
        owner_execution_id="owner",
    )
    with pytest.raises(ProviderRouteLeaseError) as dup:
        await store.release(
            lease_id=lease.lease_id,
            lease_token=lease.lease_token,
            owner_execution_id="owner",
        )
    assert dup.value.reason_code == "duplicate_release"

    lease2 = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="owner2",
        ttl_seconds=30,
        original_state={"enabled": False},
    )
    assert lease2 is not None
    with pytest.raises(ProviderRouteLeaseError) as stale:
        await store.release(
            lease_id=lease2.lease_id,
            lease_token="deadbeef",
            owner_execution_id="owner2",
        )
    assert stale.value.reason_code == "stale_lease_token"
    with pytest.raises(ProviderRouteLeaseError) as wrong_owner:
        await store.renew(
            lease_id=lease2.lease_id,
            lease_token=lease2.lease_token,
            owner_execution_id="intruder",
            ttl_seconds=30,
        )
    assert wrong_owner.value.reason_code == "stale_lease_token"
    await store.release(
        lease_id=lease2.lease_id,
        lease_token=lease2.lease_token,
        owner_execution_id="owner2",
    )


@pytest.mark.asyncio
async def test_lease_expiry_and_renewal() -> None:
    store = ProviderRouteLeaseStore()
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="owner",
        ttl_seconds=0.05,
        original_state={"enabled": False},
    )
    assert lease is not None
    await asyncio.sleep(0.08)
    expired = await store.list_expired_active()
    assert any(item.lease_id == lease.lease_id for item in expired)
    with pytest.raises(ProviderRouteLeaseError) as err:
        await store.renew(
            lease_id=lease.lease_id,
            lease_token=lease.lease_token,
            owner_execution_id="owner",
            ttl_seconds=30,
        )
    assert err.value.reason_code == "lease_expired"

    # Clear via mark_recovery so a fresh lease can renew.
    await store.mark_recovery(
        lease_id=lease.lease_id,
        lease_token=lease.lease_token,
        recovery_status="expired_no_restore",
    )
    fresh = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="owner",
        ttl_seconds=30,
        original_state={"enabled": False},
    )
    assert fresh is not None
    renewed = await store.renew(
        lease_id=fresh.lease_id,
        lease_token=fresh.lease_token,
        owner_execution_id="owner",
        ttl_seconds=60,
    )
    assert renewed.expires_at > fresh.expires_at
    await store.release(
        lease_id=fresh.lease_id,
        lease_token=fresh.lease_token,
        owner_execution_id="owner",
    )


@pytest.mark.asyncio
async def test_simulated_process_crash_and_startup_recovery(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    store = ProviderRouteLeaseStore()
    original = sanitise_route_state(
        {
            "connector_id": "opencode",
            "provider_id": "default",
            "enabled": False,
            "model_id": None,
            "configuration_status": "native",
            "available": True,
            "authenticated": True,
        }
    )
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="crashed",
        ttl_seconds=0.05,
        original_state=original,
        details={"phase": "execute"},
    )
    assert lease is not None
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        enabled = await manager.enable_route(
            "opencode",
            approval=_approval(),
            model_id="accounts/fireworks/models/deepseek-v4-flash",
        )
    assert enabled.ok
    assert (await manager.inspect_route("opencode")).enabled is True
    await asyncio.sleep(0.08)

    coordinator = ProviderRouteMutationCoordinator(store)
    reports = await coordinator.recover_expired_leases(manager)
    assert reports
    assert reports[0]["status"] == "restored_verified"
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_recovery_failure_blocks_new_mutation(
    fake_fireconnect: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(fake_fireconnect)
    store = ProviderRouteLeaseStore()
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="opencode"):
        enabled = await manager.enable_route(
            "opencode",
            approval=_approval(),
            model_id="accounts/fireworks/models/deepseek-v4-flash",
        )
    assert enabled.ok
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="crashed",
        ttl_seconds=0.05,
        original_state={"enabled": False, "connector_id": "opencode", "provider_id": "default"},
    )
    assert lease is not None
    await asyncio.sleep(0.08)
    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "restore_fail")
    coordinator = ProviderRouteMutationCoordinator(store)
    reports = await coordinator.recover_expired_leases(manager)
    assert reports[0]["status"] == "recovery_failed"

    monkeypatch.setenv("JOYMESH_FAKE_FIRECONNECT_MODE", "success")
    result = await coordinator.run_lifecycle(
        manager=manager,
        connector_id="opencode",
        approval=_approval(),
        execute=lambda: asyncio.sleep(0),
        owner_execution_id="blocked",
    )
    assert result.ok is False
    assert result.reason_code == "recovery_failed"


@pytest.mark.asyncio
async def test_multi_process_contention_via_shared_database(
    fake_fireconnect: Path, tmp_path: Path
) -> None:
    """Two stores share one DB but have separate process-local locks (multi-worker)."""

    db_path = tmp_path / "leases.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    database = Database(url)
    await database.initialize()
    try:
        store_a = ProviderRouteLeaseStore(database)
        store_b = ProviderRouteLeaseStore(database)
        manager = _manager(fake_fireconnect)
        coord_a = ProviderRouteMutationCoordinator(store_a)
        coord_b = ProviderRouteMutationCoordinator(store_b)
        order: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def job_a() -> None:
            async def execute() -> str:
                order.append("a:start")
                started.set()
                await release.wait()
                order.append("a:end")
                return "a"

            result = await coord_a.run_lifecycle(
                manager=manager,
                connector_id="opencode",
                approval=_approval(),
                execute=execute,
                owner_execution_id="proc-a",
                acquire_timeout_seconds=5.0,
            )
            assert result.ok, result.reason_code

        async def job_b() -> None:
            await started.wait()

            async def execute() -> str:
                order.append("b:start")
                return "b"

            result = await coord_b.run_lifecycle(
                manager=manager,
                connector_id="opencode",
                approval=_approval(),
                execute=execute,
                owner_execution_id="proc-b",
                acquire_timeout_seconds=5.0,
                poll_interval_seconds=0.05,
            )
            assert result.ok, result.reason_code
            order.append("b:end")

        task_a = asyncio.create_task(job_a())
        task_b = asyncio.create_task(job_b())
        await asyncio.wait_for(started.wait(), timeout=5.0)
        await asyncio.sleep(0.15)
        assert "b:start" not in order
        release.set()
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=10.0)
        assert order.index("a:end") < order.index("b:start")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_no_cross_job_provider_contamination(fake_fireconnect: Path) -> None:
    manager = _manager(fake_fireconnect)
    coordinator = ProviderRouteMutationCoordinator(ProviderRouteLeaseStore())
    seen: dict[str, bool] = {}

    async def job(name: str, model: str) -> None:
        async def execute() -> str:
            route = await manager.inspect_route("opencode")
            seen[name] = bool(route.enabled) and (model.split("/")[-1] in (route.model_id or ""))
            await asyncio.sleep(0.04)
            return name

        result = await coordinator.run_lifecycle(
            manager=manager,
            connector_id="opencode",
            approval=_approval(model=model),
            execute=execute,
            model_id=model,
            owner_execution_id=name,
            acquire_timeout_seconds=5.0,
        )
        assert result.ok
        assert result.restoration_verified

    await asyncio.gather(
        job("A", "accounts/fireworks/models/deepseek-v4-flash"),
        job("B", "accounts/fireworks/models/glm-fast-latest"),
    )
    assert seen["A"] is True
    assert seen["B"] is True
    assert (await manager.inspect_route("opencode")).enabled is False


@pytest.mark.asyncio
async def test_expired_lease_owner_cannot_continue_mutation() -> None:
    store = ProviderRouteLeaseStore()
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="opencode",
        owner_execution_id="stale",
        ttl_seconds=0.05,
        original_state={"enabled": False},
    )
    assert lease is not None
    await asyncio.sleep(0.08)
    with pytest.raises(ProviderRouteLeaseError) as err:
        await store.renew(
            lease_id=lease.lease_id,
            lease_token=lease.lease_token,
            owner_execution_id="stale",
            ttl_seconds=30,
        )
    assert err.value.reason_code == "lease_expired"


@pytest.mark.asyncio
async def test_sanitise_route_state_strips_secrets() -> None:
    cleaned = sanitise_route_state(
        {
            "connector_id": "opencode",
            "enabled": True,
            "provider_id": "fireworks",
            "model_id": "accounts/fireworks/models/x",
            "api_key": "sk-secret",
            "email": "user@example.test",
            "accountId": "acct_secret",
        }
    )
    assert "api_key" not in cleaned
    assert "email" not in cleaned
    assert "accountId" not in cleaned
    assert cleaned["enabled"] is True


@pytest.mark.asyncio
async def test_force_expire_memory_lease_for_recovery_path(fake_fireconnect: Path) -> None:
    """Simulate crash by rewriting lease expiry without releasing."""

    manager = _manager(fake_fireconnect)
    store = ProviderRouteLeaseStore()
    lease = await store.try_acquire(
        manager_id="fireconnect",
        connector_id="codex",
        owner_execution_id="crash2",
        ttl_seconds=60,
        original_state={"enabled": False, "connector_id": "codex", "provider_id": "default"},
    )
    assert lease is not None
    with mutation_authority_for_tests(manager_id="fireconnect", connector_id="codex"):
        assert (
            await manager.enable_route(
                "codex",
                approval=_approval("codex"),
                model_id="accounts/fireworks/models/deepseek-v4-flash",
            )
        ).ok
    # Force expiry in memory store.
    expired = lease.__class__(
        lease_id=lease.lease_id,
        manager_id=lease.manager_id,
        connector_id=lease.connector_id,
        owner_execution_id=lease.owner_execution_id,
        lease_token=lease.lease_token,
        status=lease.status,
        original_state=lease.original_state,
        target_provider_id=lease.target_provider_id,
        target_model_id=lease.target_model_id,
        acquired_at=lease.acquired_at,
        expires_at=utc_now() - timedelta(seconds=1),
        heartbeat_at=lease.heartbeat_at,
        recovery_status=lease.recovery_status,
        details=lease.details,
    )
    store._memory[lease.lease_id] = expired
    coordinator = ProviderRouteMutationCoordinator(store)
    reports = await coordinator.recover_expired_leases(manager, connector_ids=("codex",))
    assert reports[0]["verified"] is True
    assert (await manager.inspect_route("codex")).enabled is False
