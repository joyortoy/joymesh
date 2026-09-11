"""Strict, immutable contracts for the versioned connector catalogue."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ConnectorMaturity(StrEnum):
    CATALOGUED = "catalogued"
    DISCOVERABLE = "discoverable"
    INSTALLABLE = "installable"
    AUTHENTICATABLE = "authenticatable"
    ADAPTER_CONFORMANT = "adapter_conformant"
    REAL_BINARY_TESTED = "real_binary_tested"
    CERTIFIED = "certified"
    PRODUCTION_READY = "production_ready"
    BLOCKED = "blocked"
    DEPRECATED = "deprecated"


class ConnectorTier(StrEnum):
    TERMINAL = "terminal"
    IDE = "ide"


class ConnectorExecutionMode(StrEnum):
    STRUCTURED_HEADLESS = "structured_headless"
    TEXT_HEADLESS = "text_headless"
    PTY_INTERACTIVE = "pty_interactive"
    IDE_ONLY = "ide_only"
    DISCOVERY_ONLY = "discovery_only"
    UNSUPPORTED = "unsupported"


class PrerequisiteKind(StrEnum):
    EXECUTABLE = "executable"
    RUNTIME = "runtime"
    PACKAGE_MANAGER = "package_manager"
    OS_FEATURE = "os_feature"
    IDE = "ide"
    ACCOUNT = "account"
    SUBSCRIPTION = "subscription"


class AuthenticationStatus(StrEnum):
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    LOGIN_AVAILABLE = "login_available"
    LOGIN_IN_PROGRESS = "login_in_progress"
    DEVICE_CODE_REQUIRED = "device_code_required"
    API_KEY_REQUIRED = "api_key_required"
    CLOUD_CREDENTIALS_REQUIRED = "cloud_credentials_required"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


class OfficialSourceMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    documentation_source: HttpUrl
    source_repository: HttpUrl | None = None
    package_source: str | None = None
    verified_at: datetime
    verified_version: str | None = None
    installation_method_fingerprint: str = Field(min_length=12)

    @model_validator(mode="after")
    def timestamp_must_be_aware(self) -> OfficialSourceMetadata:
        if self.verified_at.tzinfo is None:
            raise ValueError("verified_at must include a timezone")
        return self


class InstallationOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    mechanism: str
    platforms: tuple[str, ...]
    argv: tuple[str, ...] = ()
    package_source: str
    official_origin: HttpUrl | None = None
    requires_admin: bool = False
    modifies_path: bool = False
    modifies_shell_profile: bool = False
    digest_required: bool = False
    executable: bool = True

    @model_validator(mode="after")
    def command_is_bounded(self) -> InstallationOption:
        if self.executable and not self.argv:
            raise ValueError("an executable installation option requires argv")
        forbidden = {"sh", "bash", "zsh", "cmd", "powershell", "sudo"}
        if self.argv and self.argv[0].rsplit("/", 1)[-1].lower() in forbidden:
            raise ValueError("installation plans cannot invoke shells or privilege escalation")
        if any(value in {"|", "&&", ";"} or "\x00" in value for value in self.argv):
            raise ValueError("installation argv contains a shell operator or NUL")
        if self.mechanism == "official_script" and not self.digest_required:
            raise ValueError("official scripts must be fetched and digest-bound before execution")
        return self


class Prerequisite(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: PrerequisiteKind
    name: str
    minimum_version: str | None = None
    required: bool = True


class AuthenticationMethod(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    login_argv: tuple[str, ...] = ()
    status_argv: tuple[str, ...] = ()
    environment_variables: tuple[str, ...] = ()
    interactive: bool = True


class ProviderMode(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    display_name: str
    funding_source: str
    authentication_method: str
    model_discovery_supported: bool = False
    usage_reporting_supported: bool = False
    separately_billed: bool | None = None


class CapabilityEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    declared: bool
    observed: bool = False
    certified: bool = False


class ExecutionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ConnectorExecutionMode
    argv: tuple[str, ...] = ()
    resume_argv: tuple[str, ...] = ()
    protocol: Literal["jsonl", "stream_json", "json", "text", "acp", "none"] = "none"
    task_placeholder: str = "{task}"

    @model_validator(mode="after")
    def headless_requires_task_placeholder(self) -> ExecutionDefinition:
        headless = {
            ConnectorExecutionMode.STRUCTURED_HEADLESS,
            ConnectorExecutionMode.TEXT_HEADLESS,
        }
        if self.mode in headless and self.task_placeholder not in self.argv:
            raise ValueError("headless execution argv must contain the task placeholder")
        return self


class IdeExtensionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    ecosystem: str
    identifier: str
    marketplace_url: HttpUrl


class ConnectorDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    revision: str
    harness_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str
    vendor: str
    description: str
    homepage_domain: str
    tier: ConnectorTier = ConnectorTier.TERMINAL
    category: str
    open_source: bool = False
    maturity: ConnectorMaturity
    executable_names: tuple[str, ...] = ()
    executable_kind: str = "native"
    remote_execution_supported: bool = False
    interactive_execution_supported: bool = False
    non_interactive_execution_supported: bool = False
    structured_output_supported: bool = False
    streaming_output_supported: bool = False
    session_resume_supported: bool = False
    supported_platforms: tuple[str, ...]
    supported_architectures: tuple[str, ...] = ("x86_64", "arm64")
    installation_options: tuple[InstallationOption, ...] = ()
    upgrade_options: tuple[InstallationOption, ...] = ()
    uninstall_options: tuple[InstallationOption, ...] = ()
    prerequisites: tuple[Prerequisite, ...] = ()
    authentication_methods: tuple[AuthenticationMethod, ...] = ()
    provider_modes: tuple[ProviderMode, ...] = ()
    capabilities: dict[str, CapabilityEvidence] = Field(default_factory=dict)
    execution: ExecutionDefinition
    adapter_id: str | None = None
    certification_profile_id: str | None = None
    ide_extension: IdeExtensionDefinition | None = None
    experimental: bool = True
    blocked_reason: str | None = None
    official_source: OfficialSourceMetadata

    @property
    def routable_by_maturity(self) -> bool:
        return self.maturity in {
            ConnectorMaturity.CERTIFIED,
            ConnectorMaturity.PRODUCTION_READY,
        }

    @property
    def source_review_age_days(self) -> int:
        now = datetime.now(UTC)
        return max(0, (now - self.official_source.verified_at).days)

    @model_validator(mode="after")
    def maturity_and_execution_are_consistent(self) -> ConnectorDefinition:
        if self.remote_execution_supported and not self.executable_names:
            raise ValueError("remote connectors require an executable")
        if self.tier is ConnectorTier.IDE and self.remote_execution_supported:
            raise ValueError("IDE-only definitions cannot claim remote execution")
        if (
            self.execution.mode is ConnectorExecutionMode.IDE_ONLY
            and self.tier is not ConnectorTier.IDE
        ):
            raise ValueError("IDE-only execution requires the IDE tier")
        if self.routable_by_maturity:
            if not self.adapter_id or not self.certification_profile_id:
                raise ValueError("routable maturity requires adapter and certification profile")
            if not self.remote_execution_supported:
                raise ValueError("routable maturity requires remote execution")
        if self.maturity is ConnectorMaturity.BLOCKED and not self.blocked_reason:
            raise ValueError("blocked connectors require a blocked_reason")
        return self
