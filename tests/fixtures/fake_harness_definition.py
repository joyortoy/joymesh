"""Test-only fake harness definition helper (not part of production catalogue)."""

from __future__ import annotations

from joymesh.adapters.fake import TEST_ONLY_HARNESS_ID
from joymesh.harnesses.contracts import (
    AdapterMaturity,
    CapabilityState,
    CertificationState,
    HarnessDefinition,
    OnboardingMetadata,
    ProtocolKind,
)
from joymesh.models import Capability


def fake_harness_definition() -> HarnessDefinition:
    capabilities = {
        Capability.NON_INTERACTIVE: CapabilityState.SUPPORTED,
        Capability.FILE_READ: CapabilityState.SUPPORTED,
        Capability.FILE_WRITE: CapabilityState.SUPPORTED,
        Capability.SHELL: CapabilityState.SUPPORTED,
        Capability.STREAMING: CapabilityState.SUPPORTED,
        Capability.SESSION_RESUME: CapabilityState.SUPPORTED,
        Capability.CANCELLATION: CapabilityState.SUPPORTED,
        Capability.TIMEOUT_ENFORCEMENT: CapabilityState.SUPPORTED,
        Capability.PROCESS_TREE_CLEANUP: CapabilityState.SUPPORTED,
    }
    return HarnessDefinition(
        id=TEST_ONLY_HARNESS_ID,
        display_name="Fake Harness (test-only)",
        vendor="JoyMesh",
        website="",
        documentation=(),
        executables=(),
        headless=CapabilityState.SUPPORTED,
        protocol=ProtocolKind.JSONL,
        sessions=CapabilityState.SUPPORTED,
        usage_reporting=CapabilityState.SUPPORTED,
        capabilities=capabilities,
        maturity=AdapterMaturity.STABLE,
        adapter_certification=CertificationState.ADAPTER_CERTIFIED,
        onboarding=OnboardingMetadata(
            description="Test-only deterministic harness",
            installation_methods=("bundled",),
            authentication_modes=("none",),
            supported_platforms=("darwin", "linux", "win32"),
        ),
    )
