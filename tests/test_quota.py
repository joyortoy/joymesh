"""Tests for the universal harness quota layer."""

from __future__ import annotations

import threading
import time
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
from joymesh.quota.cache import QuotaCache
from joymesh.quota.contracts import (
    HarnessAvailability,
    QuotaSnapshot,
    QuotaSource,
    QuotaState,
    QuotaVisibility,
)
from joymesh.quota.providers import (
    BaseQuotaProvider,
    ClaudeQuotaProvider,
    CodexQuotaProvider,
    GeminiQuotaProvider,
    GrokQuotaProvider,
    OpenCodeQuotaProvider,
    UnknownQuotaProvider,
)
from joymesh.quota.service import QuotaService
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh
from tests.fixtures.fake_harness_definition import fake_harness_definition


def _snap(
    harness_id: str,
    availability: HarnessAvailability,
    *,
    state: QuotaState | None = None,
    visibility: QuotaVisibility = QuotaVisibility.OBSERVED,
) -> QuotaSnapshot:
    if state is None:
        state = {
            HarnessAvailability.READY: QuotaState.AVAILABLE,
            HarnessAvailability.UNKNOWN: QuotaState.UNKNOWN,
            HarnessAvailability.QUOTA_EXHAUSTED: QuotaState.EXHAUSTED,
            HarnessAvailability.RATE_LIMITED: QuotaState.BLOCKED,
            HarnessAvailability.AUTHENTICATION_REQUIRED: QuotaState.BLOCKED,
            HarnessAvailability.CONFIGURATION_REQUIRED: QuotaState.BLOCKED,
            HarnessAvailability.OFFLINE: QuotaState.BLOCKED,
            HarnessAvailability.PROVIDER_UNAVAILABLE: QuotaState.BLOCKED,
        }.get(availability, QuotaState.UNKNOWN)
    return QuotaSnapshot(
        harness_id=harness_id,
        availability=availability,
        quota_visibility=visibility,
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
        raw_metadata={},
    )


class StaticQuotaProvider(BaseQuotaProvider):
    def __init__(self, snapshot: QuotaSnapshot) -> None:
        self.harness_id = snapshot.harness_id
        self._snapshot = snapshot
        self.calls = 0

    def quota_snapshot(self) -> QuotaSnapshot:
        self.calls += 1
        return self._snapshot


@pytest.fixture
async def mesh(tmp_path: Path):
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    instance = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    # Avoid live CLI probes during routing tests.
    instance.quota = QuotaService(
        providers={"fake": StaticQuotaProvider(_snap("fake", HarnessAvailability.READY))},
        harness_ids=("fake",),
    )
    instance.router.quota = instance.quota
    from joymesh.runtime_snapshot import RuntimeSnapshotService

    instance.runtime_snapshots = RuntimeSnapshotService(
        quota=instance.quota,
        registry=instance.registry,
        harness_ids=("fake",),
    )
    await instance.initialize()
    await instance.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="Bundled fake harness",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
            max_concurrency=8,
        )
    )
    yield instance
    await instance.close()


def test_ready_harness_provider() -> None:
    provider = StaticQuotaProvider(_snap("opencode", HarnessAvailability.READY))
    snapshot = provider.quota_snapshot()
    assert snapshot.availability is HarnessAvailability.READY
    assert snapshot.state is QuotaState.AVAILABLE
    assert snapshot.display_status == "Ready"
    assert snapshot.display_mark == "✓"


def test_authentication_required_observation() -> None:
    prior = _snap("claude-code", HarnessAvailability.READY)
    updated = ClaudeQuotaProvider().apply_observation(
        prior,
        success=False,
        failure_kind=FailureKind.AUTHENTICATION,
        detail="not logged in",
    )
    assert updated.availability is HarnessAvailability.AUTHENTICATION_REQUIRED
    assert updated.display_status == "Login required"


def test_api_key_missing_observation() -> None:
    prior = _snap("gemini-cli", HarnessAvailability.READY)
    updated = GeminiQuotaProvider().apply_observation(
        prior,
        success=False,
        failure_kind=FailureKind.INVALID_REQUEST,
        detail="Harness API key missing or invalid",
    )
    assert updated.availability is HarnessAvailability.CONFIGURATION_REQUIRED
    assert updated.display_status == "API key missing"
    assert updated.configured is False


def test_credits_exhausted_observation() -> None:
    prior = _snap("codex", HarnessAvailability.READY)
    updated = CodexQuotaProvider().apply_observation(
        prior,
        success=False,
        failure_kind=FailureKind.QUOTA_EXHAUSTED,
        detail="out of credits",
    )
    assert updated.availability is HarnessAvailability.QUOTA_EXHAUSTED
    assert updated.credits_remaining == 0.0
    assert updated.display_status == "Credits exhausted"


def test_rate_limited_observation() -> None:
    prior = _snap("grok", HarnessAvailability.READY)
    updated = GrokQuotaProvider().apply_observation(
        prior,
        success=False,
        failure_kind=FailureKind.RATE_LIMIT,
        detail="429 too many requests",
    )
    assert updated.availability is HarnessAvailability.RATE_LIMITED
    assert updated.state is QuotaState.BLOCKED


def test_unknown_provider() -> None:
    snapshot = UnknownQuotaProvider("custom-x").quota_snapshot()
    assert snapshot.availability is HarnessAvailability.UNKNOWN
    assert snapshot.quota_visibility is QuotaVisibility.UNKNOWN
    assert snapshot.source is QuotaSource.NONE


def test_provider_unavailable_observation() -> None:
    prior = _snap("opencode", HarnessAvailability.READY)
    updated = OpenCodeQuotaProvider().apply_observation(
        prior,
        success=False,
        failure_kind=FailureKind.PROCESS,
        detail="provider unavailable: upstream down",
    )
    assert updated.availability is HarnessAvailability.PROVIDER_UNAVAILABLE


def test_no_provider_support_returns_unknown() -> None:
    service = QuotaService(providers={}, harness_ids=("mystery",))
    provider = service.provider_for("mystery")
    assert isinstance(provider, UnknownQuotaProvider)
    assert provider.quota_snapshot().availability is HarnessAvailability.UNKNOWN


def test_cache_ttl_expiry() -> None:
    cache = QuotaCache(ttl_seconds=0.05)
    snap = _snap("opencode", HarnessAvailability.READY)
    cache.put(snap)
    assert cache.get("opencode") is not None
    time.sleep(0.06)
    assert cache.get("opencode") is None


@pytest.mark.asyncio
async def test_cache_refresh_on_success_and_failure() -> None:
    provider = StaticQuotaProvider(_snap("opencode", HarnessAvailability.READY))
    service = QuotaService(providers={"opencode": provider}, cache=QuotaCache(ttl_seconds=60))
    first = await service.snapshot("opencode")
    assert provider.calls == 1
    second = await service.snapshot("opencode")
    assert second is first
    assert provider.calls == 1

    await service.observe_execution("opencode", success=True)
    assert service.cache.get("opencode") is not None
    assert service.cache.get("opencode").availability is HarnessAvailability.READY

    await service.observe_execution(
        "opencode",
        success=False,
        failure_kind=FailureKind.QUOTA_EXHAUSTED,
        detail="out of credits",
    )
    failed = service.cache.get("opencode")
    assert failed is not None
    assert failed.availability is HarnessAvailability.QUOTA_EXHAUSTED


@pytest.mark.asyncio
async def test_routing_prefers_ready_over_blocked(tmp_path: Path) -> None:
    ready = StaticQuotaProvider(_snap("fake", HarnessAvailability.READY))
    # Use two fake-like providers via custom service injected into mesh.
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'route.db'}",
        registry=registry,
    )
    mesh.quota = QuotaService(providers={"fake": ready}, harness_ids=("fake",))
    mesh.router.quota = mesh.quota
    from joymesh.runtime_snapshot import RuntimeSnapshotService

    mesh.runtime_snapshots = RuntimeSnapshotService(
        quota=mesh.quota, registry=mesh.registry, harness_ids=("fake",)
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="ready",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
            max_concurrency=2,
        )
    )
    preview = await mesh.preview_routes(task="demo", workspace=tmp_path)
    assert preview.selected is not None
    assert preview.selected.harness_id == "fake"
    assert any("quota ready" in reason for reason in preview.selected.reasons)
    await mesh.close()


@pytest.mark.asyncio
async def test_routing_avoids_exhausted_unless_explicit(tmp_path: Path) -> None:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'block.db'}",
        registry=registry,
    )
    mesh.quota = QuotaService(
        providers={
            "fake": StaticQuotaProvider(_snap("fake", HarnessAvailability.QUOTA_EXHAUSTED)),
        },
        harness_ids=("fake",),
    )
    mesh.router.quota = mesh.quota
    from joymesh.runtime_snapshot import RuntimeSnapshotService

    mesh.runtime_snapshots = RuntimeSnapshotService(
        quota=mesh.quota, registry=mesh.registry, harness_ids=("fake",)
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="exhausted",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
            max_concurrency=2,
        )
    )
    auto = await mesh.preview_routes(task="demo", workspace=tmp_path)
    assert auto.selected is None
    fake = next(c for c in auto.candidates if c.harness_id == "fake")
    assert fake.eligible is False
    assert any("quota_exhausted" in reason for reason in fake.reasons)

    explicit = await mesh.preview_routes(
        task="demo",
        workspace=tmp_path,
        preferred_harness="fake",
        allowed_harnesses=frozenset({"fake"}),
    )
    selected = explicit.selected
    assert selected is not None
    assert selected.harness_id == "fake"
    assert selected.eligible is True
    assert any("explicit override" in reason for reason in selected.reasons)
    await mesh.close()


@pytest.mark.asyncio
async def test_execution_observation_updates_quota(mesh: JoyMesh) -> None:
    await mesh.quota.observe_execution(
        "fake",
        success=False,
        failure_kind=FailureKind.AUTHENTICATION,
        detail="login required",
    )
    snap = await mesh.quota.snapshot("fake")
    assert snap.availability is HarnessAvailability.AUTHENTICATION_REQUIRED

    await mesh.quota.observe_execution("fake", success=True)
    snap = await mesh.quota.snapshot("fake")
    assert snap.availability is HarnessAvailability.READY


def test_cli_status_and_json_output() -> None:
    runner = CliRunner()
    ready = _snap("opencode", HarnessAvailability.READY)
    login = _snap("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED)
    credits = _snap("codex", HarnessAvailability.QUOTA_EXHAUSTED)
    key = _snap("gemini-cli", HarnessAvailability.CONFIGURATION_REQUIRED)
    grok = _snap("grok", HarnessAvailability.READY)
    providers = {
        item.harness_id: StaticQuotaProvider(item)
        for item in (ready, login, credits, key, grok)
    }

    class _Mesh:
        def __init__(self) -> None:
            self.quota = QuotaService(providers=providers)

        async def list_quota(self, **kwargs: Any) -> tuple[QuotaSnapshot, ...]:
            return await self.quota.list_snapshots(
                harness_ids=kwargs.get("harness_ids"),
                refresh=kwargs.get("refresh", False),
            )

        async def close(self) -> None:
            return None

    with patch("joymesh.cli.JoyMesh", _Mesh):
        status = runner.invoke(cli_app, ["quota", "status"])
        assert status.exit_code == 0, status.output
        assert "OpenCode" in status.output
        assert "Ready" in status.output
        assert "Login required" in status.output
        assert "Credits exhausted" in status.output
        assert "API key missing" in status.output
        assert "Grok" in status.output

        json_result = runner.invoke(cli_app, ["quota", "json"])
        assert json_result.exit_code == 0, json_result.output
        assert '"harness_id": "opencode"' in json_result.output
        assert '"availability": "ready"' in json_result.output


def test_api_quota_endpoints(tmp_path: Path) -> None:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        registry=registry,
    )
    mesh.quota = QuotaService(
        providers={
            "opencode": StaticQuotaProvider(_snap("opencode", HarnessAvailability.READY)),
            "claude-code": StaticQuotaProvider(
                _snap("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED)
            ),
            "codex": StaticQuotaProvider(_snap("codex", HarnessAvailability.QUOTA_EXHAUSTED)),
            "gemini-cli": StaticQuotaProvider(
                _snap("gemini-cli", HarnessAvailability.CONFIGURATION_REQUIRED)
            ),
            "grok": StaticQuotaProvider(_snap("grok", HarnessAvailability.READY)),
        }
    )
    app = create_app(mesh=mesh)
    with TestClient(app) as client:
        listed = client.get("/quota")
        assert listed.status_code == 200
        body = listed.json()
        assert isinstance(body, list)
        by_id = {item["harness_id"]: item for item in body}
        assert by_id["opencode"]["availability"] == "ready"
        assert by_id["claude-code"]["availability"] == "authentication_required"
        assert by_id["codex"]["availability"] == "quota_exhausted"
        assert by_id["gemini-cli"]["availability"] == "configuration_required"

        one = client.get("/quota/codex")
        assert one.status_code == 200
        assert one.json()["availability"] == "quota_exhausted"

        refreshed = client.post("/quota/refresh", json={"harness_id": "opencode"})
        assert refreshed.status_code == 200
        assert refreshed.json()[0]["harness_id"] == "opencode"


def test_cache_thread_safety() -> None:
    cache = QuotaCache(ttl_seconds=60)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            for step in range(50):
                snap = _snap(f"h{index % 3}", HarnessAvailability.READY)
                cache.put(snap)
                cache.get(snap.harness_id)
                if step % 7 == 0:
                    cache.invalidate(snap.harness_id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_gemini_configuration_required_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("joymesh.quota.providers.shutil.which", lambda _name: "/bin/gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_GCA", raising=False)
    with patch("joymesh.quota.providers.Path.is_file", return_value=False):
        snap = GeminiQuotaProvider().quota_snapshot()
    assert snap.availability is HarnessAvailability.CONFIGURATION_REQUIRED


def test_codex_offline_when_missing() -> None:
    with patch("joymesh.quota.providers.shutil.which", return_value=None):
        snap = CodexQuotaProvider().quota_snapshot()
    assert snap.availability is HarnessAvailability.OFFLINE


def test_format_table_matches_completion_criteria() -> None:
    service = QuotaService(providers={})
    table = service.format_table(
        [
            _snap("opencode", HarnessAvailability.READY),
            _snap("claude-code", HarnessAvailability.AUTHENTICATION_REQUIRED),
            _snap("codex", HarnessAvailability.QUOTA_EXHAUSTED),
            _snap("gemini-cli", HarnessAvailability.CONFIGURATION_REQUIRED),
            _snap("grok", HarnessAvailability.READY),
        ]
    )
    assert "OpenCode" in table and "✓ Ready" in table
    assert "Claude" in table and "⚠ Login required" in table
    assert "Codex" in table and "⚠ Credits exhausted" in table
    assert "Gemini" in table and "⚠ API key missing" in table
    assert "Grok" in table and "✓ Ready" in table
