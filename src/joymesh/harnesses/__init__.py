"""Data-driven harness lifecycle architecture."""

from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.harnesses.contracts import (
    ApprovalToken,
    AuthenticationState,
    CapabilityState,
    CertificationState,
    HarnessDefinition,
    HarnessInstallation,
    LifecyclePlan,
)
from joymesh.harnesses.discovery import DiscoveryPolicy, HarnessDiscovery
from joymesh.harnesses.lifecycle import HarnessLifecycleService
from joymesh.harnesses.registry import HarnessRegistry

__all__ = [
    "ApprovalToken",
    "AuthenticationState",
    "CapabilityState",
    "CertificationState",
    "DiscoveryPolicy",
    "HarnessDefinition",
    "HarnessDiscovery",
    "HarnessInstallation",
    "HarnessLifecycleService",
    "HarnessRegistry",
    "LifecyclePlan",
    "builtin_catalogue",
]
