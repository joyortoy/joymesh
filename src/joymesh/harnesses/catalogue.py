"""Compatibility projection of the versioned connector catalogue."""

from __future__ import annotations

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.models import (
    ConnectorDefinition,
    ConnectorExecutionMode,
    ConnectorMaturity,
    InstallationOption,
)
from joymesh.harnesses.contracts import (
    AdapterMaturity,
    AuthenticationMethod,
    CapabilityState,
    CertificationState,
    CommandTemplate,
    DocumentationReference,
    HarnessDefinition,
    InstallSource,
    OnboardingMetadata,
    ProtocolKind,
)
from joymesh.models import Capability

_ALIASES: dict[str, tuple[str, ...]] = {
    "amazon-q": ("amazon-q-developer",),
    "claude-code": ("claude",),
    "gemini-cli": ("gemini",),
    "github-copilot": ("copilot",),
    "opencode": ("open-code",),
    "roo-code": ("roo",),
    "factory-droid": ("droid",),
    "warp": ("oz",),
}

_CAPABILITY_KEYS: dict[str, Capability] = {
    "read_files": Capability.FILE_READ,
    "write_files": Capability.FILE_WRITE,
    "run_commands": Capability.SHELL,
    "use_network": Capability.NETWORK_SANDBOX,
    "use_mcp": Capability.MCP,
    "use_images": Capability.IMAGE_INPUT,
    "structured_output": Capability.STRUCTURED_EVENTS,
    "streaming_output": Capability.STREAMING,
    "session_resume": Capability.SESSION_RESUME,
    "approval_mode": Capability.APPROVAL_MODES,
    "sandbox_mode": Capability.FILESYSTEM_SANDBOX,
    "model_selection": Capability.MODEL_SELECTION,
    "provider_selection": Capability.PROVIDER_SELECTION,
    "usage_reporting": Capability.USAGE_REPORTING,
    "native_cancellation": Capability.CANCELLATION,
}


def _source(mechanism: str) -> InstallSource:
    return {
        "homebrew": InstallSource.HOMEBREW,
        "npm": InstallSource.NPM,
        "pipx": InstallSource.PIPX,
        "uv": InstallSource.UV,
        "cargo": InstallSource.CARGO,
    }.get(mechanism, InstallSource.STANDALONE)


def _commands(
    options: tuple[InstallationOption, ...],
) -> tuple[CommandTemplate, ...]:
    commands: list[CommandTemplate] = []
    for option in options:
        if not option.executable or not option.argv:
            continue
        commands.append(
            CommandTemplate(
                source=_source(option.mechanism),
                argv=option.argv,
                platforms=frozenset(option.platforms),
                checksum_available=bool(option.digest_required),
            )
        )
    return tuple(commands)


def _protocol(definition: ConnectorDefinition) -> ProtocolKind:
    return {
        "jsonl": ProtocolKind.JSONL,
        "stream_json": ProtocolKind.STREAM_JSON,
        "json": ProtocolKind.JSON,
        "text": ProtocolKind.TEXT,
        "acp": ProtocolKind.ACP,
        "none": ProtocolKind.NONE,
    }[definition.execution.protocol]


def _state(value: bool) -> CapabilityState:
    return CapabilityState.SUPPORTED if value else CapabilityState.UNSUPPORTED


def _maturity(definition: ConnectorDefinition) -> AdapterMaturity:
    if definition.maturity in {
        ConnectorMaturity.BLOCKED,
        ConnectorMaturity.CATALOGUED,
        ConnectorMaturity.DISCOVERABLE,
    } or definition.execution.mode in {
        ConnectorExecutionMode.IDE_ONLY,
        ConnectorExecutionMode.DISCOVERY_ONLY,
        ConnectorExecutionMode.UNSUPPORTED,
    }:
        return AdapterMaturity.DISCOVERY_ONLY
    if definition.maturity in {
        ConnectorMaturity.CERTIFIED,
        ConnectorMaturity.PRODUCTION_READY,
    }:
        return AdapterMaturity.STABLE
    if definition.maturity in {
        ConnectorMaturity.ADAPTER_CONFORMANT,
        ConnectorMaturity.REAL_BINARY_TESTED,
    }:
        return AdapterMaturity.BETA
    return AdapterMaturity.EXPERIMENTAL


def _legacy(definition: ConnectorDefinition) -> HarnessDefinition:
    capabilities = {
        capability: _state(evidence.declared)
        for key, evidence in definition.capabilities.items()
        if (capability := _CAPABILITY_KEYS.get(key)) is not None
    }
    if definition.non_interactive_execution_supported:
        capabilities[Capability.NON_INTERACTIVE] = CapabilityState.SUPPORTED
    if definition.remote_execution_supported:
        capabilities.update(
            {
                Capability.WORKING_DIRECTORY: CapabilityState.SUPPORTED,
                Capability.CANCELLATION: CapabilityState.SUPPORTED,
                Capability.TIMEOUT_ENFORCEMENT: CapabilityState.SUPPORTED,
                Capability.PROCESS_TREE_CLEANUP: CapabilityState.SUPPORTED,
            }
        )
    authentication = tuple(
        AuthenticationMethod(
            kind=method.kind,
            login_argv=method.login_argv or None,
            environment_variables=method.environment_variables,
            interactive=method.interactive,
        )
        for method in definition.authentication_methods
    )
    source = definition.official_source
    unsupported_reason = definition.blocked_reason
    if definition.harness_id == "amazon-q":
        unsupported_reason = "official_general_chat_headless_contract_not_documented"
    if definition.execution.mode is ConnectorExecutionMode.IDE_ONLY:
        unsupported_reason = "official_machine_interface_not_verified"
    return HarnessDefinition(
        id=definition.harness_id,
        display_name=definition.display_name,
        vendor=definition.vendor,
        website=f"https://{definition.homepage_domain}/",
        documentation=(
            DocumentationReference(
                title="Official connector documentation",
                url=str(source.documentation_source),
            ),
        ),
        executables=definition.executable_names,
        aliases=_ALIASES.get(definition.harness_id, ()),
        platforms=frozenset(definition.supported_platforms),
        install=_commands(definition.installation_options),
        upgrade=_commands(definition.upgrade_options),
        uninstall=_commands(definition.uninstall_options),
        authentication=authentication,
        environment_variables=tuple(
            sorted(
                {
                    key
                    for method in definition.authentication_methods
                    for key in method.environment_variables
                }
            )
        ),
        headless=_state(definition.non_interactive_execution_supported),
        protocol=_protocol(definition),
        sessions=_state(definition.session_resume_supported),
        model_selection=capabilities.get(Capability.MODEL_SELECTION, CapabilityState.UNKNOWN),
        provider_selection=capabilities.get(Capability.PROVIDER_SELECTION, CapabilityState.UNKNOWN),
        usage_reporting=capabilities.get(Capability.USAGE_REPORTING, CapabilityState.UNKNOWN),
        cancellation=capabilities.get(Capability.CANCELLATION, CapabilityState.UNKNOWN),
        sandbox_controls=capabilities.get(Capability.FILESYSTEM_SANDBOX, CapabilityState.UNKNOWN),
        capabilities=capabilities,
        maturity=_maturity(definition),
        adapter_certification=(
            CertificationState.FAKE_BINARY_CONFORMANCE_PASSED
            if definition.maturity is ConnectorMaturity.ADAPTER_CONFORMANT
            else CertificationState.UNCERTIFIED_ADAPTER
        ),
        unsupported_reason=unsupported_reason,
        onboarding=OnboardingMetadata(
            description=definition.description,
            setup_difficulty="moderate",
            installation_methods=tuple(
                option.mechanism for option in definition.installation_options
            ),
            authentication_modes=tuple(method.kind for method in definition.authentication_methods),
            subscription_or_funding_modes=tuple(
                mode.funding_source for mode in definition.provider_modes
            ),
            can_install_automatically=any(
                option.executable for option in definition.installation_options
            ),
            can_verify_subscription=False,
            requires_paid_inference=any(
                mode.separately_billed is True for mode in definition.provider_modes
            ),
            supported_platforms=definition.supported_platforms,
            known_limitations=((unsupported_reason,) if unsupported_reason else ()),
        ),
    )


def _fake() -> HarnessDefinition:
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
        id="fake",
        display_name="Bundled fake harness",
        vendor="JoyMesh",
        website="https://github.com/joyortoy/joymesh",
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
            description=(
                "Deterministic local harness for tests; never evidence for binary certification."
            ),
            installation_methods=("bundled",),
            authentication_modes=("none",),
            supported_platforms=("darwin", "linux", "win32"),
        ),
    )


def builtin_catalogue() -> tuple[HarnessDefinition, ...]:
    """Return the legacy SDK projection of the packaged connector catalogue."""

    entries = [_fake()]
    entries.extend(_legacy(item) for item in ConnectorCatalogue.builtins().all())
    return tuple(sorted(entries, key=lambda entry: entry.id))


def render_capability_matrix(
    catalogue: tuple[HarnessDefinition, ...] | None = None,
) -> str:
    """Generate the compact documentation matrix from catalogue data."""

    definitions = catalogue or builtin_catalogue()
    lines = [
        "# Generated harness capability matrix",
        "",
        (
            "This file is generated from the versioned connector catalogue; "
            "edit its YAML, not this table."
        ),
        "",
        "| Harness | Headless | Protocol | Sessions | Usage | Sandbox | Maturity |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        (
            f"| `{definition.id}` | {definition.headless.value} | "
            f"{definition.protocol.value} | {definition.sessions.value} | "
            f"{definition.usage_reporting.value} | {definition.sandbox_controls.value} | "
            f"{definition.maturity.value} |"
        )
        for definition in definitions
    )
    return "\n".join(lines) + "\n"
