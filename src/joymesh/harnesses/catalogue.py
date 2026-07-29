"""Official-source-backed built-in harness catalogue."""

from __future__ import annotations

from joymesh.harnesses.contracts import (
    AdapterMaturity,
    AuthenticationMethod,
    CapabilityState,
    CertificationState,
    CommandTemplate,
    DocumentationReference,
    HarnessDefinition,
    InstallSource,
    ProtocolKind,
)
from joymesh.models import Capability

_STANDARD_CAPABILITIES = {
    Capability.NON_INTERACTIVE: CapabilityState.SUPPORTED,
    Capability.FILE_READ: CapabilityState.SUPPORTED,
    Capability.FILE_WRITE: CapabilityState.SUPPORTED,
    Capability.SHELL: CapabilityState.SUPPORTED,
    Capability.STREAMING: CapabilityState.SUPPORTED,
    Capability.STRUCTURED_EVENTS: CapabilityState.SUPPORTED,
    Capability.SESSION_CREATE: CapabilityState.SUPPORTED,
    Capability.TOOL_USE: CapabilityState.SUPPORTED,
    Capability.TOOL_PERMISSIONS: CapabilityState.REQUIRES_CONFIGURATION,
    Capability.WORKING_DIRECTORY: CapabilityState.SUPPORTED,
    Capability.CANCELLATION: CapabilityState.SUPPORTED,
    Capability.TIMEOUT_ENFORCEMENT: CapabilityState.SUPPORTED,
    Capability.PROCESS_TREE_CLEANUP: CapabilityState.SUPPORTED,
}


def _doc(title: str, url: str) -> DocumentationReference:
    return DocumentationReference(title=title, url=url)


def _npm(package: str) -> tuple[CommandTemplate, ...]:
    return (
        CommandTemplate(
            source=InstallSource.NPM,
            argv=("npm", "install", "--global", package),
        ),
    )


def _npm_upgrade(package: str) -> tuple[CommandTemplate, ...]:
    return (
        CommandTemplate(
            source=InstallSource.NPM,
            argv=("npm", "update", "--global", package),
        ),
    )


def builtin_catalogue() -> tuple[HarnessDefinition, ...]:
    """Return a fresh deterministic catalogue with no mutable global registry."""

    entries = (
        HarnessDefinition(
            id="fake",
            display_name="Bundled fake harness",
            vendor="JoyMesh",
            website="https://github.com/joyortoy/joymesh",
            documentation=(),
            executables=(),
            platforms=frozenset({"darwin", "linux", "win32"}),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.JSONL,
            sessions=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.STABLE,
            adapter_certification=CertificationState.ADAPTER_CERTIFIED,
        ),
        HarnessDefinition(
            id="codex",
            display_name="OpenAI Codex CLI",
            vendor="OpenAI",
            website="https://openai.com/codex/",
            documentation=(
                _doc("Codex CLI reference", "https://developers.openai.com/codex/cli/reference/"),
                _doc(
                    "Non-interactive mode",
                    "https://developers.openai.com/codex/noninteractive/",
                ),
            ),
            executables=("codex",),
            aliases=("openai-codex", "codex-cli"),
            install=_npm("@openai/codex"),
            upgrade=_npm_upgrade("@openai/codex"),
            uninstall=(
                CommandTemplate(
                    source=InstallSource.NPM,
                    argv=("npm", "uninstall", "--global", "@openai/codex"),
                ),
            ),
            authentication=(AuthenticationMethod(kind="oauth", login_argv=("codex", "login")),),
            config_locations=("~/.codex/config.toml",),
            environment_variables=("OPENAI_API_KEY",),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.JSONL,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.BETA,
            adapter_certification=CertificationState.FAKE_BINARY_CONFORMANCE_PASSED,
        ),
        HarnessDefinition(
            id="opencode",
            display_name="OpenCode",
            vendor="Anomaly",
            website="https://opencode.ai/",
            documentation=(
                _doc("OpenCode CLI", "https://opencode.ai/docs/cli/"),
                _doc("OpenCode server", "https://opencode.ai/docs/server/"),
            ),
            executables=("opencode",),
            aliases=("open-code",),
            install=_npm("opencode-ai"),
            upgrade=_npm_upgrade("opencode-ai"),
            uninstall=(
                CommandTemplate(
                    source=InstallSource.NPM,
                    argv=("npm", "uninstall", "--global", "opencode-ai"),
                ),
            ),
            authentication=(
                AuthenticationMethod(
                    kind="interactive",
                    login_argv=("opencode", "auth", "login"),
                ),
            ),
            config_locations=("~/.config/opencode/opencode.json", "opencode.json"),
            environment_variables=("OPENCODE_CONFIG", "OPENCODE_SERVER_PASSWORD"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.JSONL,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            provider_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            sandbox_controls=CapabilityState.UNKNOWN,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.BETA,
            adapter_certification=CertificationState.FAKE_BINARY_CONFORMANCE_PASSED,
        ),
        HarnessDefinition(
            id="claude-code",
            display_name="Claude Code",
            vendor="Anthropic",
            website="https://www.anthropic.com/claude-code",
            documentation=(
                _doc(
                    "Claude Code CLI reference",
                    "https://docs.anthropic.com/en/docs/claude-code/cli-reference",
                ),
            ),
            executables=("claude",),
            aliases=("claude",),
            install=_npm("@anthropic-ai/claude-code"),
            upgrade=_npm_upgrade("@anthropic-ai/claude-code"),
            uninstall=(
                CommandTemplate(
                    source=InstallSource.NPM,
                    argv=("npm", "uninstall", "--global", "@anthropic-ai/claude-code"),
                ),
            ),
            authentication=(AuthenticationMethod(kind="oauth", login_argv=("claude",)),),
            config_locations=("~/.claude/settings.json", ".claude/settings.json"),
            environment_variables=("ANTHROPIC_API_KEY",),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.STREAM_JSON,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.BETA,
            adapter_certification=CertificationState.FAKE_BINARY_CONFORMANCE_PASSED,
        ),
        HarnessDefinition(
            id="gemini-cli",
            display_name="Gemini CLI",
            vendor="Google",
            website="https://github.com/google-gemini/gemini-cli",
            documentation=(
                _doc(
                    "Gemini headless mode",
                    "https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md",
                ),
            ),
            executables=("gemini",),
            aliases=("gemini",),
            install=_npm("@google/gemini-cli"),
            upgrade=_npm_upgrade("@google/gemini-cli"),
            uninstall=(
                CommandTemplate(
                    source=InstallSource.NPM,
                    argv=("npm", "uninstall", "--global", "@google/gemini-cli"),
                ),
            ),
            authentication=(AuthenticationMethod(kind="oauth", login_argv=("gemini",)),),
            config_locations=("~/.gemini/settings.json", ".gemini/settings.json"),
            environment_variables=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.STREAM_JSON,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.BETA,
            adapter_certification=CertificationState.FAKE_BINARY_CONFORMANCE_PASSED,
        ),
        HarnessDefinition(
            id="github-copilot",
            display_name="GitHub Copilot CLI",
            vendor="GitHub",
            website="https://github.com/features/copilot/cli",
            documentation=(
                _doc(
                    "Copilot CLI programmatic reference",
                    "https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference",
                ),
            ),
            executables=("copilot",),
            aliases=("copilot-cli",),
            install=_npm("@github/copilot"),
            upgrade=_npm_upgrade("@github/copilot"),
            authentication=(AuthenticationMethod(kind="oauth", login_argv=("copilot", "/login")),),
            config_locations=("~/.copilot/settings.json",),
            environment_variables=("COPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "COPILOT_HOME"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.JSONL,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.UNKNOWN,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.EXPERIMENTAL,
        ),
        HarnessDefinition(
            id="aider",
            display_name="Aider",
            vendor="Aider AI",
            website="https://aider.chat/",
            documentation=(_doc("Scripting Aider", "https://aider.chat/docs/scripting.html"),),
            executables=("aider",),
            install=(
                CommandTemplate(
                    source=InstallSource.PIPX,
                    argv=("pipx", "install", "aider-chat"),
                ),
            ),
            upgrade=(
                CommandTemplate(
                    source=InstallSource.PIPX,
                    argv=("pipx", "upgrade", "aider-chat"),
                ),
            ),
            authentication=(AuthenticationMethod(kind="api_key", interactive=False),),
            config_locations=("~/.aider.conf.yml", ".aider.conf.yml", ".env"),
            environment_variables=("AIDER_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.TEXT,
            sessions=CapabilityState.UNSUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            provider_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.UNKNOWN,
            sandbox_controls=CapabilityState.UNSUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {
                Capability.STREAMING: CapabilityState.SUPPORTED,
                Capability.SESSION_RESUME: CapabilityState.UNSUPPORTED,
            },
            maturity=AdapterMaturity.EXPERIMENTAL,
        ),
        HarnessDefinition(
            id="goose",
            display_name="Goose",
            vendor="Agentic AI Foundation",
            website="https://block.github.io/goose/",
            documentation=(
                _doc("Goose CLI", "https://block.github.io/goose/docs/guides/goose-cli-commands/"),
            ),
            executables=("goose",),
            authentication=(AuthenticationMethod(kind="provider_configuration"),),
            config_locations=("~/.config/goose/config.yaml",),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.TEXT,
            sessions=CapabilityState.UNKNOWN,
            provider_selection=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.UNKNOWN},
            maturity=AdapterMaturity.EXPERIMENTAL,
            unsupported_reason="structured_output_contract_not_documented",
        ),
        HarnessDefinition(
            id="pi",
            display_name="Pi coding agent",
            vendor="Mario Zechner",
            website="https://github.com/badlogic/pi-mono",
            documentation=(
                _doc(
                    "Pi coding agent",
                    "https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent",
                ),
            ),
            executables=("pi",),
            install=_npm("@mariozechner/pi-coding-agent"),
            upgrade=_npm_upgrade("@mariozechner/pi-coding-agent"),
            authentication=(AuthenticationMethod(kind="provider_configuration"),),
            config_locations=("~/.pi/agent/settings.json",),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.JSONL,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            provider_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.EXPERIMENTAL,
        ),
        HarnessDefinition(
            id="continue",
            display_name="Continue CLI",
            vendor="Continue",
            website="https://continue.dev/",
            documentation=(
                _doc("Continue CLI quickstart", "https://docs.continue.dev/cli/quickstart"),
                _doc("Continue headless mode", "https://docs.continue.dev/cli/headless-mode"),
            ),
            executables=("cn",),
            aliases=("continue-cli",),
            authentication=(AuthenticationMethod(kind="oauth", login_argv=("cn", "login")),),
            environment_variables=("CONTINUE_API_KEY", "ANTHROPIC_API_KEY"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.TEXT,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.EXPERIMENTAL,
        ),
        HarnessDefinition(
            id="amazon-q",
            display_name="Amazon Q Developer CLI",
            vendor="Amazon Web Services",
            website="https://aws.amazon.com/q/developer/",
            documentation=(
                _doc(
                    "Amazon Q Developer command line",
                    "https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line.html",
                ),
            ),
            executables=("q", "qchat"),
            aliases=("amazon-q-developer",),
            authentication=(AuthenticationMethod(kind="builder_id", login_argv=("q", "login")),),
            config_locations=("~/.aws/amazonq/",),
            headless=CapabilityState.UNSUPPORTED,
            protocol=ProtocolKind.NONE,
            capabilities={
                Capability.FILE_READ: CapabilityState.UNKNOWN,
                Capability.FILE_WRITE: CapabilityState.UNKNOWN,
                Capability.SHELL: CapabilityState.UNKNOWN,
                Capability.STREAMING: CapabilityState.UNSUPPORTED,
                Capability.SESSION_RESUME: CapabilityState.UNKNOWN,
                Capability.TOOL_USE: CapabilityState.UNKNOWN,
            },
            maturity=AdapterMaturity.DISCOVERY_ONLY,
            unsupported_reason="official_general_chat_headless_contract_not_documented",
        ),
        HarnessDefinition(
            id="qwen-code",
            display_name="Qwen Code",
            vendor="Alibaba Cloud",
            website="https://github.com/QwenLM/qwen-code",
            documentation=(
                _doc(
                    "Qwen Code headless mode",
                    "https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/",
                ),
            ),
            executables=("qwen",),
            install=_npm("@qwen-code/qwen-code"),
            upgrade=_npm_upgrade("@qwen-code/qwen-code"),
            authentication=(AuthenticationMethod(kind="oauth_or_api_key"),),
            config_locations=("~/.qwen/settings.json",),
            environment_variables=("QWEN_API_KEY", "OPENAI_API_KEY"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.STREAM_JSON,
            sessions=CapabilityState.SUPPORTED,
            model_selection=CapabilityState.SUPPORTED,
            provider_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.SUPPORTED,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {Capability.SESSION_RESUME: CapabilityState.SUPPORTED},
            maturity=AdapterMaturity.EXPERIMENTAL,
        ),
        HarnessDefinition(
            id="cline",
            display_name="Cline CLI",
            vendor="Cline",
            website="https://cline.bot/",
            documentation=(
                _doc("Cline CLI overview", "https://docs.cline.bot/usage/cli-overview"),
            ),
            executables=("cline",),
            install=_npm("cline"),
            upgrade=_npm_upgrade("cline"),
            authentication=(
                AuthenticationMethod(kind="provider_auth", login_argv=("cline", "auth")),
            ),
            config_locations=("~/.cline/", ".cline/"),
            headless=CapabilityState.SUPPORTED,
            protocol=ProtocolKind.JSON,
            sessions=CapabilityState.UNKNOWN,
            model_selection=CapabilityState.SUPPORTED,
            provider_selection=CapabilityState.SUPPORTED,
            usage_reporting=CapabilityState.UNKNOWN,
            sandbox_controls=CapabilityState.SUPPORTED,
            capabilities=_STANDARD_CAPABILITIES
            | {
                Capability.STREAMING: CapabilityState.UNKNOWN,
                Capability.SESSION_RESUME: CapabilityState.UNKNOWN,
            },
            maturity=AdapterMaturity.EXPERIMENTAL,
        ),
        HarnessDefinition(
            id="roo-code",
            display_name="Roo Code CLI",
            vendor="Roo Code",
            website="https://roocode.com/",
            documentation=(
                _doc("Roo Code CLI releases", "https://github.com/RooCodeInc/Roo-Code/releases"),
            ),
            executables=("roo",),
            aliases=("roo",),
            authentication=(AuthenticationMethod(kind="provider_auth"),),
            headless=CapabilityState.EXPERIMENTAL,
            protocol=ProtocolKind.JSONL,
            sessions=CapabilityState.EXPERIMENTAL,
            model_selection=CapabilityState.EXPERIMENTAL,
            provider_selection=CapabilityState.EXPERIMENTAL,
            usage_reporting=CapabilityState.UNKNOWN,
            capabilities={
                capability: CapabilityState.EXPERIMENTAL for capability in _STANDARD_CAPABILITIES
            }
            | {Capability.SESSION_RESUME: CapabilityState.EXPERIMENTAL},
            maturity=AdapterMaturity.EXPERIMENTAL,
            unsupported_reason="official_cli_is_pre_release",
        ),
    )
    return tuple(sorted(entries, key=lambda entry: entry.id))


def render_capability_matrix(
    catalogue: tuple[HarnessDefinition, ...] | None = None,
) -> str:
    """Generate the compact documentation matrix from catalogue data."""

    definitions = catalogue or builtin_catalogue()
    lines = [
        "# Generated harness capability matrix",
        "",
        "This file is generated from `builtin_catalogue()`; edit the catalogue, not this table.",
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
