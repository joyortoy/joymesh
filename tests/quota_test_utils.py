"""Helpers to isolate unit tests from live CLI quota probes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from joymesh.quota.contracts import (
    HarnessAvailability,
    QuotaSnapshot,
    QuotaSource,
    QuotaState,
    QuotaVisibility,
)
from joymesh.quota.providers import BaseQuotaProvider
from joymesh.quota.service import QuotaService
from joymesh.runtime_snapshot import RuntimeSnapshotService
from joymesh.service import JoyMesh


class ReadyQuotaProvider(BaseQuotaProvider):
    def __init__(self, harness_id: str) -> None:
        self.harness_id = harness_id

    def quota_snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            harness_id=self.harness_id,
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
            source=QuotaSource.NONE,
            raw_metadata={"test": "ready_quota"},
        )


def install_ready_quota(
    mesh: JoyMesh,
    harness_ids: Iterable[str] | None = None,
) -> None:
    """Replace live quota probes with READY snapshots for fake-executable tests."""

    ids = (
        tuple(harness_ids)
        if harness_ids is not None
        else tuple(adapter.manifest.harness_id for adapter in mesh.registry.list())
    )
    providers: dict[str, BaseQuotaProvider] = {
        harness_id: ReadyQuotaProvider(harness_id) for harness_id in ids
    }
    mesh.quota = QuotaService(providers=providers, harness_ids=ids)
    mesh.router.quota = mesh.quota
    mesh.runtime_snapshots = RuntimeSnapshotService(
        quota=mesh.quota,
        registry=mesh.registry,
        harness_ids=ids,
    )
