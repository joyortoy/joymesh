import pytest

from joymesh.adapters.base import UnsupportedFeatureError
from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.models import Capability, EventType, HarnessAvailability
from joymesh.registry import AdapterRegistry


async def test_registry_detects_bundled_fake_harness() -> None:
    registry = AdapterRegistry()

    detected = await registry.detect()

    assert {item.manifest.harness_id for item in detected} == {
        "codex",
        "fake",
        "opencode",
    }
    fake = next(item for item in detected if item.manifest.harness_id == "fake")
    assert fake.availability is HarnessAvailability.AVAILABLE


def test_fake_adapter_normalizes_native_progress() -> None:
    adapter = FakeHarnessAdapter()

    event = adapter.normalize_output(
        run_id="run-1",
        sequence=3,
        stream="stdout",
        line='{"type":"progress","message":"50%"}',
    )

    assert event.event.type is EventType.HARNESS_PROGRESS
    assert event.event.message == "50%"
    assert event.event.payload["native_type"] == "progress"


def test_capabilities_serialize_in_stable_order() -> None:
    manifest = FakeHarnessAdapter().manifest

    assert manifest.model_dump(mode="json")["capabilities"] == [
        "file.read",
        "file.write",
        "session.resume",
        "shell",
        "streaming",
    ]


def test_unsupported_feature_is_reported() -> None:
    with pytest.raises(UnsupportedFeatureError, match=r"tool\.use"):
        FakeHarnessAdapter().require_feature(Capability.TOOL_USE)
