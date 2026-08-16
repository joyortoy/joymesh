"""Tests for JoyMesh → JoyCLI runtime snapshot protocol."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.api import create_app
from joymesh.cli import app as cli_app
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import BillingRoute, FailureKind, SubscriptionCreate
from joymesh.quota.contracts import (
    HarnessAvailability,
    QuotaSnapshot,
    QuotaSource,
    QuotaState,
    QuotaVisibility,
)
from joymesh.quota.providers import BaseQuotaProvider
from joymesh.quota.service import QuotaService
from joymesh.registry import AdapterRegistry
from joymesh.runtime_snapshot import (
    RuntimeLaunchError,
    RuntimeSnapshot,
    RuntimeSnapshotCache,
    RuntimeSnapshotService,
    RuntimeSnapshotValidationError,
    RuntimeValidationCode,
)
from joymesh.runtime_snapshot.contracts import (
    SCHEMA_VERSION,
    ExecutionState,
    HarnessRuntimeSnapshot,
    LatencySnapshot,
    QualityLevel,
    QualitySnapshot,
    UsageSnapshot,
)
from joymesh.runtime_snapshot.validators import assert_privacy, validate_snapshot
from joymesh.service import JoyMesh, NoRouteError
from tests.fixtures.fake_harness_definition import fake_harness_definition
from tests.quota_test_utils import install_ready_quota


def _quota(
    harness_id: str,
    availability: HarnessAvailability,
) -> QuotaSnapshot:
    state = {
        HarnessAvailability.READY: QuotaState.AVAILABLE,
        HarnessAvailability.QUOTA_EXHAUSTED: QuotaState.EXHAUSTED,
        HarnessAvailability.AUTHENTICATION_REQUIRED: QuotaState.BLOCKED,
        HarnessAvailability.CONFIGURATION_REQUIRED: QuotaState.BLOCKED,
        HarnessAvailability.RATE_LIMITED: QuotaState.BLOCKED,
        HarnessAvailability.OFFLINE: QuotaState.BLOCKED,
        HarnessAvailability.PROVIDER_UNAVAILABLE: QuotaState.BLOCKED,
        HarnessAvailability.UNKNOWN: QuotaState.UNKNOWN,
    }[availability]
    return QuotaSnapshot(
        harness_id=harness_id,
        availability=availability,
        quota_visibility=QuotaVisibility.OBSERVED,
        state=state,
        authenticated=availability is HarnessAvailability.READY,
        configured=availability
        not in {
            HarnessAvailability.CONFIGURATION_REQUIRED,
            HarnessAvailability.OFFLINE,
        },
        credits_remaining=0.0 if availability is HarnessAvailability.QUOTA_EXHAUSTED else None,
        requests_remaining=None,
        tokens_remaining=None,
        reset_at=None,
        observed_at=datetime.now(UTC),
        source=QuotaSource.OFFICIAL_CLI,
        raw_metadata={"detail": "unit"},
    )


class StaticQuotaProvider(BaseQuotaProvider):
    def __init__(self, snapshot: QuotaSnapshot) -> None:
        self.harness_id = snapshot.harness_id
        self._snapshot = snapshot

    def quota_snapshot(self) -> QuotaSnapshot:
        return self._snapshot


def _service(
    *snapshots: QuotaSnapshot,
    registry: AdapterRegistry | None = None,
) -> RuntimeSnapshotService:
    providers = {item.harness_id: StaticQuotaProvider(item) for item in snapshots}
    quota = QuotaService(
        providers=providers,
        harness_ids=tuple(providers),
    )
    reg = registry or AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    return RuntimeSnapshotService(
        quota=quota,
        registry=reg,
        harness_ids=tuple(providers),
    )


@pytest.mark.asyncio
async def test_snapshot_generation() -> None:
    service = _service(
        _quota("opencode", HarnessAvailability.READY),
        _quota("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED),
    )
    snapshot = await service.snapshot(
        harness_ids=("opencode", "claude-code"),
        refresh=True,
    )
    assert snapshot.schema_version == SCHEMA_VERSION
    assert snapshot.snapshot_id
    assert {item.harness_id for item in snapshot.harnesses} == {"opencode", "claude-code"}
    opencode = snapshot.harness("opencode")
    assert opencode is not None
    assert opencode.availability is HarnessAvailability.READY
    assert opencode.execution_state is ExecutionState.IDLE
    claude = snapshot.harness("claude-code")
    assert claude is not None
    assert claude.availability is HarnessAvailability.AUTHENTICATION_REQUIRED


@pytest.mark.asyncio
async def test_snapshot_refresh_and_cache() -> None:
    service = _service(_quota("opencode", HarnessAvailability.READY))
    first = await service.snapshot(refresh=True)
    second = await service.snapshot(refresh=False)
    assert second.snapshot_id == first.snapshot_id
    third = await service.refresh()
    assert third.snapshot_id != first.snapshot_id


def test_snapshot_cache_ttl_expiry() -> None:
    cache = RuntimeSnapshotCache(ttl_seconds=0.01)
    snap = RuntimeSnapshot(
        snapshot_id="s1",
        observed_at=datetime.now(UTC),
        harnesses=(),
        schema_version=SCHEMA_VERSION,
    )
    cache.put(snap)
    assert cache.get() is not None
    import time

    time.sleep(0.02)
    assert cache.get() is None


def test_duplicate_harness_ids_rejected() -> None:
    quota = _quota("opencode", HarnessAvailability.READY)
    entry = HarnessRuntimeSnapshot(
        harness_id="opencode",
        availability=HarnessAvailability.READY,
        authenticated=True,
        configured=True,
        quota=quota,
        capabilities=frozenset(),
        execution_state=ExecutionState.IDLE,
        recent_usage=UsageSnapshot(),
        recent_quality=QualitySnapshot(),
        latency=LatencySnapshot(),
        provider_metadata={},
    )
    snapshot = RuntimeSnapshot(
        snapshot_id="x",
        observed_at=datetime.now(UTC),
        harnesses=(entry, entry),
        schema_version=SCHEMA_VERSION,
    )
    with pytest.raises(RuntimeSnapshotValidationError, match="duplicate"):
        validate_snapshot(snapshot)


@pytest.mark.asyncio
async def test_quota_auth_config_usage_quality_latency_updates() -> None:
    service = _service(_quota("opencode", HarnessAvailability.READY))
    await service.observe_execution(
        "opencode",
        success=True,
        duration_ms=120.0,
        input_tokens=3,
        output_tokens=5,
    )
    entry = await service.harness_snapshot("opencode", refresh=True)
    assert entry.recent_usage.input_tokens == 3
    assert entry.recent_usage.output_tokens == 5
    assert entry.recent_usage.total_tokens == 8
    assert entry.recent_usage.execution_count == 1
    assert entry.recent_quality.level is QualityLevel.GOOD
    assert entry.latency.last_ms == 120.0
    assert entry.latency.average_ms == 120.0
    assert entry.latency.p95_ms == 120.0

    await service.observe_execution(
        "opencode",
        success=False,
        failure_kind=FailureKind.AUTHENTICATION,
        duration_ms=50.0,
    )
    entry = await service.harness_snapshot("opencode", refresh=False)
    assert entry.availability is HarnessAvailability.AUTHENTICATION_REQUIRED
    assert entry.recent_quality.level is QualityLevel.BAD
    assert entry.authenticated is False


@pytest.mark.asyncio
async def test_configuration_update() -> None:
    service = _service(_quota("gemini-cli", HarnessAvailability.READY))
    await service.observe_execution(
        "gemini-cli",
        success=False,
        failure_kind=FailureKind.INVALID_REQUEST,
        detail="Harness API key missing or invalid",
    )
    entry = await service.harness_snapshot("gemini-cli", refresh=False)
    assert entry.availability is HarnessAvailability.CONFIGURATION_REQUIRED
    assert entry.configured is False


@pytest.mark.asyncio
async def test_launch_time_revalidation_and_runtime_changed() -> None:
    service = _service(_quota("codex", HarnessAvailability.READY))
    await service.snapshot(refresh=True)
    # Flip provider to exhausted and refresh.
    exhausted = _quota("codex", HarnessAvailability.QUOTA_EXHAUSTED)
    service.quota = QuotaService(
        providers={"codex": StaticQuotaProvider(exhausted)},
        harness_ids=("codex",),
    )
    with pytest.raises(RuntimeLaunchError) as excinfo:
        await service.revalidate_for_launch(
            "codex",
            prior_availability=HarnessAvailability.READY,
        )
    assert excinfo.value.code is RuntimeValidationCode.RUNTIME_CHANGED


@pytest.mark.asyncio
async def test_launch_rejects_authentication_required() -> None:
    service = _service(
        _quota("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED)
    )
    with pytest.raises(RuntimeLaunchError) as excinfo:
        await service.revalidate_for_launch("claude-code")
    assert excinfo.value.code is RuntimeValidationCode.AUTHENTICATION_REQUIRED


@pytest.mark.asyncio
async def test_start_run_revalidation_surfaces_structured_error(tmp_path: Path) -> None:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'launch.db'}",
        registry=registry,
    )
    providers = {
        "fake": StaticQuotaProvider(_quota("fake", HarnessAvailability.QUOTA_EXHAUSTED))
    }
    mesh.quota = QuotaService(providers=providers, harness_ids=("fake",))
    mesh.router.quota = mesh.quota
    mesh.runtime_snapshots = RuntimeSnapshotService(
        quota=mesh.quota, registry=mesh.registry, harness_ids=("fake",)
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="fake",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    from joymesh.models import RouteCandidate, RunRequest

    request = RunRequest(task="x", workspace=str(tmp_path))
    route = RouteCandidate(
        harness_id="fake",
        subscription_id=(await mesh.list_subscriptions())[0].id,
        score=1.0,
        eligible=True,
        reasons=("forced",),
    )
    with pytest.raises(NoRouteError) as excinfo:
        await mesh.start_run(request=request, route=route)
    assert excinfo.value.code == "quota_exhausted"
    await mesh.close()


def test_json_serialization_and_privacy() -> None:
    quota = _quota("opencode", HarnessAvailability.READY)
    entry = HarnessRuntimeSnapshot(
        harness_id="opencode",
        availability=HarnessAvailability.READY,
        authenticated=True,
        configured=True,
        quota=quota,
        capabilities=frozenset({"streaming"}),
        execution_state=ExecutionState.IDLE,
        recent_usage=UsageSnapshot(input_tokens=1, output_tokens=2, total_tokens=3),
        recent_quality=QualitySnapshot(level=QualityLevel.GOOD),
        latency=LatencySnapshot(average_ms=10.0, last_ms=10.0, p95_ms=10.0),
        provider_metadata={"quota_visibility": "observed", "api_key": "SECRET"},
    )
    # Sanitize would drop api_key at build time; assert_privacy catches leaks.
    dirty = RuntimeSnapshot(
        snapshot_id="s",
        observed_at=datetime.now(UTC),
        harnesses=(entry,),
        schema_version=SCHEMA_VERSION,
    )
    with pytest.raises(RuntimeSnapshotValidationError, match="privacy"):
        assert_privacy(dirty.as_dict())


@pytest.mark.asyncio
async def test_provider_metadata_sanitized() -> None:
    snap = _quota("opencode", HarnessAvailability.READY)
    object.__setattr__(
        snap,
        "raw_metadata",
        {"version": "1.0", "api_key": "SECRET", "token": "x"},
    )
    # QuotaSnapshot is frozen — rebuild instead.
    snap = QuotaSnapshot(
        harness_id="opencode",
        availability=HarnessAvailability.READY,
        quota_visibility=QuotaVisibility.OBSERVED,
        state=QuotaState.AVAILABLE,
        authenticated=True,
        configured=True,
        credits_remaining=None,
        requests_remaining=None,
        tokens_remaining=None,
        reset_at=None,
        observed_at=datetime.now(UTC),
        source=QuotaSource.OFFICIAL_CLI,
        raw_metadata={"version": "1.0", "api_key": "SECRET", "token": "x"},
    )
    service = _service(snap)
    entry = await service.harness_snapshot("opencode", refresh=True)
    assert "api_key" not in entry.provider_metadata
    assert "token" not in entry.provider_metadata
    assert entry.provider_metadata.get("version") == "1.0"
    assert_privacy(entry.as_dict())


def test_cli_runtime_output() -> None:
    runner = CliRunner()

    class _Mesh:
        def __init__(self) -> None:
            self.runtime_snapshots = _service(
                _quota("opencode", HarnessAvailability.READY),
                _quota("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED),
                _quota("codex", HarnessAvailability.READY),
                _quota("gemini-cli", HarnessAvailability.CONFIGURATION_REQUIRED),
                _quota("grok", HarnessAvailability.READY),
            )

        async def get_runtime_snapshot(self, **kwargs: Any) -> RuntimeSnapshot:
            return await self.runtime_snapshots.snapshot(
                harness_ids=(
                    "opencode",
                    "claude-code",
                    "codex",
                    "gemini-cli",
                    "grok",
                ),
                refresh=True,
            )

        async def close(self) -> None:
            return None

    with patch("joymesh.cli.JoyMesh", _Mesh):
        result = runner.invoke(cli_app, ["runtime"])
        assert result.exit_code == 0, result.output
        assert "OpenCode" in result.output
        assert "Ready" in result.output
        assert "Authentication Required" in result.output
        assert "Configuration Required" in result.output

        json_result = runner.invoke(cli_app, ["runtime", "json"])
        assert json_result.exit_code == 0, json_result.output
        assert '"schema_version"' in json_result.output


def test_api_runtime_snapshot(tmp_path: Path) -> None:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        registry=registry,
    )
    install_ready_quota(mesh, harness_ids=("fake", "opencode", "claude-code"))
    # Override with mixed statuses for API shape.
    mixed = {
        "opencode": StaticQuotaProvider(_quota("opencode", HarnessAvailability.READY)),
        "claude-code": StaticQuotaProvider(
            _quota("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED)
        ),
        "fake": StaticQuotaProvider(_quota("fake", HarnessAvailability.READY)),
    }
    mesh.quota = QuotaService(providers=mixed, harness_ids=tuple(mixed))
    mesh.router.quota = mesh.quota
    mesh.runtime_snapshots = RuntimeSnapshotService(
        quota=mesh.quota, registry=mesh.registry, harness_ids=tuple(mixed)
    )
    app = create_app(mesh=mesh)
    with TestClient(app) as client:
        listed = client.get("/runtime/snapshot")
        assert listed.status_code == 200
        body = listed.json()
        assert body["schema_version"] == SCHEMA_VERSION
        assert "harnesses" in body
        one = client.get("/runtime/snapshot/opencode")
        assert one.status_code == 200
        assert one.json()["availability"] == "ready"
        refreshed = client.post("/runtime/snapshot/refresh", json={})
        assert refreshed.status_code == 200
        assert refreshed.json()["snapshot_id"]


def test_observation_store_thread_safety() -> None:
    service = _service(_quota("opencode", HarnessAvailability.READY))
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            for step in range(40):
                service.observations.record_execution(
                    "opencode",
                    success=step % 2 == 0,
                    failure_kind=None if step % 2 == 0 else FailureKind.PROCESS,
                    duration_ms=float(step),
                    input_tokens=1,
                    output_tokens=1,
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    usage = service.observations.usage("opencode")
    assert usage.execution_count == 240


@pytest.mark.asyncio
async def test_execution_updates_via_mesh(tmp_path: Path) -> None:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'exec.db'}",
        registry=registry,
    )
    install_ready_quota(mesh)
    await mesh.initialize()
    profile = await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="fake",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    from joymesh.models import RouteCandidate, RunRequest, RunStatus

    request = RunRequest(task="NORMAL", workspace=str(tmp_path))
    route = RouteCandidate(
        harness_id="fake",
        subscription_id=profile.id,
        score=1.0,
        eligible=True,
        reasons=("forced",),
    )
    run = await mesh.start_run(request=request, route=route)
    completed = await mesh.wait(run.id)
    assert completed.status is RunStatus.COMPLETED
    snapshot = await mesh.get_runtime_snapshot(refresh=True)
    entry = snapshot.harness("fake")
    assert entry is not None
    assert entry.recent_usage.execution_count >= 1
    assert entry.recent_quality.level is QualityLevel.GOOD
    await mesh.close()
