from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import Capability
from joymesh.registry import AdapterRegistry
from tests.fixtures.fake_harness_definition import fake_harness_definition


def _test_registry() -> AdapterRegistry:
    return AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )


async def test_registry_detects_bundled_fake_harness_when_explicit() -> None:
    registry = _test_registry()
    detected = await registry.detect()
    assert any(item.manifest.harness_id == "fake" for item in detected)
    fake = next(item for item in detected if item.manifest.harness_id == "fake")
    assert fake.executable


async def test_fake_adapter_detect() -> None:
    adapter = FakeHarnessAdapter()
    descriptor = await adapter.detect()
    assert descriptor.manifest.harness_id == "fake"


async def test_fake_manifest_capabilities() -> None:
    manifest = FakeHarnessAdapter().manifest
    assert Capability.STREAMING in manifest.capabilities


async def test_fake_require_feature() -> None:
    FakeHarnessAdapter().require_feature(Capability.STREAMING)
