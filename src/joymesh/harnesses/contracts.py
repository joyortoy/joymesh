"""Immutable contracts for harness discovery and lifecycle operations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from joymesh.models import Capability, utc_now


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    EXPERIMENTAL = "experimental"
    REQUIRES_CONFIGURATION = "requires_configuration"


class AdapterMaturity(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"
    DISCOVERY_ONLY = "discovery_only"


class CertificationState(StrEnum):
    UNCERTIFIED_ADAPTER = "uncertified_adapter"
    ADAPTER_CERTIFIED = "adapter_certified"
    FAKE_BINARY_CONFORMANCE_PASSED = "fake_binary_conformance_passed"
    UNCERTIFIED_BINARY = "uncertified_binary"
    REAL_BINARY_DETECTED = "real_binary_detected"
    REAL_BINARY_SMOKE_PASSED = "real_binary_smoke_passed"
    BINARY_CERTIFIED = "binary_certified"
    UNSUPPORTED = "unsupported"
    TEMPORARILY_BROKEN = "temporarily_broken"
    VERSION_OUTSIDE_CERTIFIED_RANGE = "version_outside_certified_range"
    FAILED = "failed"
    STALE = "stale"


class AuthenticationState(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    MISCONFIGURED = "misconfigured"
    INTERACTIVE_LOGIN_REQUIRED = "interactive_login_required"


class FundingKind(StrEnum):
    SUBSCRIPTION = "subscription"
    API = "api"
    LOCAL = "local"
    UNKNOWN = "unknown"


class BillingMode(StrEnum):
    INCLUDED_ALLOWANCE = "included_allowance"
    PAID_API = "paid_api"
    LOCAL_COMPUTE = "local_compute"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ProtocolKind(StrEnum):
    JSONL = "jsonl"
    STREAM_JSON = "stream_json"
    JSON = "json"
    TEXT = "text"
    SDK = "sdk"
    ACP = "acp"
    SERVER = "server"
    NONE = "none"


class RouteTransformKind(StrEnum):
    FIRECONNECT = "fireconnect"
    FIREWORKS_ROUTER = "fireworks_router"
    OPENAI_COMPATIBLE = "openai_compatible"


class InstallSource(StrEnum):
    HOMEBREW = "homebrew"
    NPM = "npm"
    PIPX = "pipx"
    UV = "uv"
    CARGO = "cargo"
    STANDALONE = "standalone"
    PATH = "path"
    OVERRIDE = "override"


class LifecycleAction(StrEnum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    UNINSTALL = "uninstall"
    LOGIN = "login"
    CERTIFY = "certify"
    ROUTE_TRANSFORM = "route_transform"


class DocumentationReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    url: str


class CommandTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: InstallSource
    argv: tuple[str, ...]
    platforms: frozenset[str] = frozenset({"darwin", "linux", "win32"})
    checksum_available: bool = False


class AuthenticationMethod(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    login_argv: tuple[str, ...] | None = None
    environment_variables: tuple[str, ...] = ()
    interactive: bool = True


class OnboardingMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str = ""
    recommended_for: tuple[str, ...] = ()
    setup_difficulty: str = "moderate"
    installation_methods: tuple[str, ...] = ()
    authentication_modes: tuple[str, ...] = ()
    subscription_or_funding_modes: tuple[str, ...] = ()
    can_install_automatically: bool = False
    can_verify_subscription: bool = False
    certification_cost: str = "unknown"
    requires_paid_inference: bool = False
    default_paid_route_policy: str = "ask"
    fireconnect_compatible: bool = False
    supported_platforms: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()


class HarnessDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str
    vendor: str
    website: str
    documentation: tuple[DocumentationReference, ...]
    executables: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    platforms: frozenset[str] = frozenset({"darwin", "linux", "win32"})
    install: tuple[CommandTemplate, ...] = ()
    upgrade: tuple[CommandTemplate, ...] = ()
    uninstall: tuple[CommandTemplate, ...] = ()
    version_args: tuple[str, ...] = ("--version",)
    version_pattern: str | None = None
    authentication: tuple[AuthenticationMethod, ...] = ()
    config_locations: tuple[str, ...] = ()
    environment_variables: tuple[str, ...] = ()
    headless: CapabilityState = CapabilityState.UNKNOWN
    protocol: ProtocolKind = ProtocolKind.NONE
    sessions: CapabilityState = CapabilityState.UNKNOWN
    model_selection: CapabilityState = CapabilityState.UNKNOWN
    provider_selection: CapabilityState = CapabilityState.UNKNOWN
    usage_reporting: CapabilityState = CapabilityState.UNKNOWN
    cancellation: CapabilityState = CapabilityState.SUPPORTED
    sandbox_controls: CapabilityState = CapabilityState.UNKNOWN
    capabilities: dict[Capability, CapabilityState] = Field(default_factory=dict)
    maturity: AdapterMaturity = AdapterMaturity.EXPERIMENTAL
    adapter_certification: CertificationState = CertificationState.UNCERTIFIED_ADAPTER
    unsupported_reason: str | None = None
    onboarding: OnboardingMetadata = Field(default_factory=OnboardingMetadata)


class DiscoveryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: InstallSource
    candidate: str
    reason: str
    precedence: int = Field(ge=0)


class HarnessInstallation(BaseModel):
    model_config = ConfigDict(frozen=True)

    harness_id: str
    executable: str
    version: str | None = None
    source: InstallSource
    detected: bool = True
    authenticated: AuthenticationState = AuthenticationState.UNKNOWN
    certification: CertificationState = CertificationState.UNCERTIFIED_BINARY
    evidence: tuple[DiscoveryEvidence, ...] = ()


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    harness_id: str
    installations: tuple[HarnessInstallation, ...]
    unavailable_reason: str | None = None


class FundingSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: FundingKind
    provider: str
    account_label: str | None = None
    plan_name: str | None = None
    billing_mode: BillingMode = BillingMode.UNKNOWN
    confidence: Confidence = Confidence.UNKNOWN


class RouteTransform(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: RouteTransformKind
    provider: str
    model: str
    endpoint: str | None = None
    funding: FundingSource
    requires_approval: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


class ApprovalToken(BaseModel):
    """Explicit, action-bound approval supplied by an SDK consumer."""

    model_config = ConfigDict(frozen=True)

    action: LifecycleAction
    harness_id: str
    approved: bool
    nonce: str = Field(min_length=8)


class LifecyclePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    action: LifecycleAction
    harness_id: str
    argv: tuple[str, ...]
    source: InstallSource | None = None
    requires_approval: bool = True
    requires_admin: bool = False
    dry_run: bool = True
    notes: tuple[str, ...] = ()


class LifecycleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str
    return_code: int
    stdout: str
    stderr: str
    installation: HarnessInstallation | None = None


class CertificationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    harness_id: str
    adapter_version: str
    binary_version: str | None
    executable: str | None
    state: CertificationState
    checks: dict[str, bool]
    detail: str | None = None
    recorded_at: datetime = Field(default_factory=utc_now)
    joymesh_version: str = "0.1.0"
    operating_system: str
    test_suite_version: str = "1"
    diagnostics: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessInspection(BaseModel):
    model_config = ConfigDict(frozen=True)

    definition: HarnessDefinition
    discovery: DiscoveryResult
    authentication: AuthenticationState = AuthenticationState.UNKNOWN
    authentication_detail: str | None = None
    certifications: tuple[CertificationEvidence, ...] = ()
