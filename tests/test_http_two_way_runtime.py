"""HTTP two-way JoyCTL ↔ JoyMesh runtime bridge tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from joymesh.api import create_app
from joymesh.delivery.factory import build_delivery_transport
from joymesh.delivery.settings import (
    DeliverySettings,
    DeliveryTransportMode,
    resolve_delivery_settings,
)
from joymesh.sdk.http_runtime import snapshot_to_sdk


class _FakeSnapshots:
    def as_json(self, snapshot: Any) -> dict[str, Any]:
        del snapshot
        return {
            "snapshot_id": "rs_test",
            "organisation_id": "org_x",
            "observed_at": "2026-08-09T00:00:00+00:00",
            "harnesses": [
                {
                    "harness_id": "gemini-cli",
                    "availability": "ready",
                    "authenticated": True,
                    "configured": True,
                    "capabilities": ["code"],
                }
            ],
            "reason_codes": ["runtime_ok"],
        }


class _FakeMesh:
    runtime_snapshots = _FakeSnapshots()

    async def get_runtime_snapshot(self) -> object:
        return object()

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def list_connectors(self) -> tuple[Any, ...]:
        return ()


def test_snapshot_to_sdk_maps_harnesses() -> None:
    sdk = snapshot_to_sdk(
        {
            "snapshot_id": "rs1",
            "harnesses": [
                {"harness_id": "opencode", "availability": "ready", "authenticated": True}
            ],
        }
    )
    assert sdk.snapshot_id == "rs1"
    assert sdk.harnesses[0]["harness_id"] == "opencode"
    assert sdk.source_component == "joymesh"


def test_v1_runtime_snapshot_and_execute_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOYMESH_JOYCTL_BASE_URL", raising=False)
    app = create_app(mesh=_FakeMesh())  # type: ignore[arg-type]
    client = TestClient(app)

    snap = client.get("/v1/runtime/snapshot")
    assert snap.status_code == 200
    body = snap.json()
    assert body["schema"] == "joy.runtime_snapshot/v1"
    assert body["harnesses"][0]["harness_id"] == "gemini-cli"

    rejected = client.post(
        "/v1/runtime/execute",
        json={
            "execution_id": "ex1",
            "attempt_id": "at1",
            "organisation_id": "org_x",
            "selected_harness": "gemini-cli",
            "routing_decision_id": "rd1",
            "authorization_reference": "auth1",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert "placement_required" in rejected.json()["reason_codes"]

    accepted = client.post(
        "/v1/runtime/execute",
        json={
            "execution_id": "ex2",
            "attempt_id": "at2",
            "organisation_id": "org_x",
            "selected_harness": "gemini-cli",
            "routing_decision_id": "rd1",
            "authorization_reference": "auth1",
            "placement_id": "plc_1",
        },
    )
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["ok"] is True
    assert payload["status"] == "accepted"
    assert payload["backend_id"] == "joymesh-http-runtime"


def test_http_delivery_settings_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_DELIVERY_TRANSPORT", "http")
    monkeypatch.setenv("JOYMESH_JOYCTL_BASE_URL", "http://127.0.0.1:8765")
    settings = resolve_delivery_settings()
    assert settings.transport is DeliveryTransportMode.HTTP
    assert settings.http_base_url == "http://127.0.0.1:8765"
    transport = build_delivery_transport(settings)
    assert transport.name == "http"


def test_http_delivery_requires_base_url() -> None:
    with pytest.raises(Exception) as exc:
        build_delivery_transport(
            DeliverySettings(transport=DeliveryTransportMode.HTTP, http_base_url=None)
        )
    assert "http" in str(exc.value).lower() or "base" in str(exc.value).lower()
