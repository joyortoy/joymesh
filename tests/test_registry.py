from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.models import EventType, HarnessAvailability
from joymesh.registry import AdapterRegistry


async def test_registry_detects_bundled_fake_harness() -> None:
    registry = AdapterRegistry()

    detected = await registry.detect()

    assert len(detected) == 1
    assert detected[0].manifest.harness_id == "fake"
    assert detected[0].availability is HarnessAvailability.AVAILABLE


def test_fake_adapter_normalizes_native_progress() -> None:
    adapter = FakeHarnessAdapter()

    event = adapter.normalize_output(
        run_id="run-1",
        sequence=3,
        stream="stdout",
        line='{"type":"progress","message":"50%"}',
    )

    assert event.type is EventType.HARNESS_PROGRESS
    assert event.message == "50%"
    assert event.payload["native_type"] == "progress"


def test_capabilities_serialize_in_stable_order() -> None:
    manifest = FakeHarnessAdapter().manifest

    assert manifest.model_dump(mode="json")["capabilities"] == [
        "file.read",
        "file.write",
        "shell",
        "streaming",
    ]
