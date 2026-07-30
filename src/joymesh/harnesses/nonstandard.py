"""Nonstandard / custom harness adapters — same contract, untrusted config."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from joymesh.adapters.base import HarnessAdapter
from joymesh.adapters.fake import REMOVED_PRODUCTION_HARNESS_IDS
from joymesh.config import CustomHarnessConfig
from joymesh.harnesses.contracts import (
    AdapterMaturity,
    CapabilityState,
    CertificationState,
    HarnessDefinition,
    OnboardingMetadata,
    ProtocolKind,
)
from joymesh.models import (
    AdapterObservation,
    Capability,
    CapabilityManifest,
    EventType,
    HarnessAvailability,
    HarnessDescriptor,
    LaunchSpec,
    NormalizedEvent,
    RunRequest,
    SupportStatus,
)
from joymesh.security import filter_environment, redact_secrets

_HARNESS_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SUPPORTED_INPUT = frozenset({"stdin", "argv"})
_SUPPORTED_OUTPUT = frozenset({"jsonl", "json", "text"})
_SUPPORTED_CWD = frozenset({"inherit", "workspace"})
_MAX_TIMEOUT = 86_400
_SHELL_METACHARS = frozenset("|&;<>$`\n\r")
_KNOWN_CAPABILITY_VALUES = frozenset(item.value for item in Capability)


@dataclass(frozen=True)
class CustomHarnessValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class CustomHarnessValidationResult:
    ok: bool
    issues: tuple[CustomHarnessValidationIssue, ...]

    @property
    def errors(self) -> tuple[CustomHarnessValidationIssue, ...]:
        return self.issues


@dataclass(frozen=True)
class CustomHarnessReadiness:
    harness_id: str
    ready: bool
    checks: dict[str, dict[str, str]]

    def as_dict(self) -> dict[str, object]:
        return {
            "harness_id": self.harness_id,
            "ready": self.ready,
            "checks": self.checks,
        }


def validate_custom_harness_config(config: CustomHarnessConfig) -> CustomHarnessValidationResult:
    issues: list[CustomHarnessValidationIssue] = []
    if not _HARNESS_ID_RE.match(config.harness_id):
        issues.append(
            CustomHarnessValidationIssue(
                "invalid_harness_id",
                "harness_id must be lowercase alphanumeric with _/- (2-64 chars)",
            )
        )
    if config.harness_id in REMOVED_PRODUCTION_HARNESS_IDS:
        issues.append(
            CustomHarnessValidationIssue(
                "forbidden_harness_id",
                f"harness_id '{config.harness_id}' is reserved/removed",
            )
        )
    if not config.executable or not str(config.executable).strip():
        issues.append(
            CustomHarnessValidationIssue("missing_executable", "executable is required")
        )
    else:
        executable = str(config.executable)
        if any(ch in executable for ch in _SHELL_METACHARS) or "|" in executable:
            issues.append(
                CustomHarnessValidationIssue(
                    "shell_interpolation_forbidden",
                    "executable must not contain shell metacharacters; use args arrays",
                )
            )
        if " " in executable and not Path(executable).exists():
            issues.append(
                CustomHarnessValidationIssue(
                    "executable_must_be_path_or_name",
                    "use a single executable path/name; put flags in args",
                )
            )
    for arg in config.args:
        if not isinstance(arg, str):
            issues.append(
                CustomHarnessValidationIssue(
                    "args_must_be_strings",
                    "args must be a structured array of strings",
                )
            )
            break
        if any(ch in arg for ch in ("\n", "\r")):
            issues.append(
                CustomHarnessValidationIssue(
                    "args_forbid_newlines",
                    "args must not contain newlines",
                )
            )
    if config.input_mode not in _SUPPORTED_INPUT:
        issues.append(
            CustomHarnessValidationIssue(
                "unsupported_input_mode",
                f"input_mode must be one of {sorted(_SUPPORTED_INPUT)}",
            )
        )
    if config.output_mode not in _SUPPORTED_OUTPUT:
        issues.append(
            CustomHarnessValidationIssue(
                "unsupported_output_mode",
                f"output_mode must be one of {sorted(_SUPPORTED_OUTPUT)}",
            )
        )
    if config.working_directory not in _SUPPORTED_CWD:
        issues.append(
            CustomHarnessValidationIssue(
                "unsupported_working_directory",
                f"working_directory must be one of {sorted(_SUPPORTED_CWD)}",
            )
        )
    if config.timeout_seconds < 1 or config.timeout_seconds > _MAX_TIMEOUT:
        issues.append(
            CustomHarnessValidationIssue(
                "timeout_out_of_range",
                f"timeout_seconds must be between 1 and {_MAX_TIMEOUT}",
            )
        )
    for key in config.environment_allowlist:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            issues.append(
                CustomHarnessValidationIssue(
                    "invalid_env_key",
                    f"invalid environment allowlist key: {key}",
                )
            )
        lowered = key.lower()
        if any(token in lowered for token in ("key", "token", "secret", "password", "credential")):
            issues.append(
                CustomHarnessValidationIssue(
                    "credentials_forbidden_in_env",
                    f"environment allowlist must not include credential-like keys: {key}",
                )
            )
    for name in config.capabilities:
        if name not in _KNOWN_CAPABILITY_VALUES:
            issues.append(
                CustomHarnessValidationIssue(
                    "unknown_capability",
                    f"unknown capability '{name}'; must be a known Capability value",
                )
            )
    return CustomHarnessValidationResult(ok=not issues, issues=tuple(issues))


def custom_capability_set(config: CustomHarnessConfig) -> frozenset[Capability]:
    """Parse declared custom capabilities; unknown names are rejected at validate time."""

    supported: set[Capability] = set()
    for name in config.capabilities:
        if name not in _KNOWN_CAPABILITY_VALUES:
            continue
        supported.add(Capability(name))
    return frozenset(supported)


def assess_custom_harness_readiness(config: CustomHarnessConfig) -> CustomHarnessReadiness:
    checks: dict[str, dict[str, str]] = {}
    validation = validate_custom_harness_config(config)
    checks["configured"] = {
        "status": "passed" if validation.ok else "failed",
        "detail": "; ".join(item.message for item in validation.issues) or "ok",
    }
    resolved = shutil.which(config.executable) or (
        config.executable if Path(config.executable).is_file() else None
    )
    if resolved:
        path = Path(resolved)
        executable_ok = os.access(path, os.X_OK)
        checks["executable"] = {
            "status": "passed" if executable_ok else "failed",
            "detail": str(path),
        }
        checks["permissions"] = {
            "status": "passed" if executable_ok else "failed",
            "detail": "executable bit" if executable_ok else "not executable",
        }
    else:
        checks["executable"] = {"status": "failed", "detail": "not found"}
        checks["permissions"] = {"status": "failed", "detail": "executable missing"}
    checks["version"] = {"status": "unknown", "detail": "custom harness has no version probe"}
    checks["authentication"] = {
        "status": "passed",
        "detail": "custom harness auth is operator-managed",
    }
    checks["protocol"] = {
        "status": "passed" if config.output_mode in _SUPPORTED_OUTPUT else "failed",
        "detail": config.output_mode,
    }
    ready = all(
        checks[name]["status"] == "passed"
        for name in ("configured", "executable", "permissions", "protocol")
    )
    checks["execution"] = {
        "status": "passed" if ready else "failed",
        "detail": "ready" if ready else "not ready",
    }
    return CustomHarnessReadiness(harness_id=config.harness_id, ready=ready, checks=checks)


def custom_harness_definition(config: CustomHarnessConfig) -> HarnessDefinition:
    declared = custom_capability_set(config)
    capability_states = {
        capability: CapabilityState.SUPPORTED for capability in declared
    }
    return HarnessDefinition(
        id=config.harness_id,
        display_name=config.display_name,
        vendor="custom",
        website="",
        documentation=(),
        executables=(Path(config.executable).name,),
        headless=CapabilityState.SUPPORTED,
        protocol={
            "jsonl": ProtocolKind.JSONL,
            "json": ProtocolKind.JSON,
            "text": ProtocolKind.TEXT,
        }.get(config.output_mode, ProtocolKind.TEXT),
        sessions=CapabilityState.UNSUPPORTED,
        usage_reporting=CapabilityState.UNSUPPORTED,
        capabilities=capability_states,
        maturity=AdapterMaturity.EXPERIMENTAL,
        adapter_certification=CertificationState.UNCERTIFIED_ADAPTER,
        onboarding=OnboardingMetadata(
            description=f"Custom harness: {config.display_name}",
            installation_methods=("custom",),
            authentication_modes=("operator",),
        ),
    )


class CustomHarnessAdapter(HarnessAdapter):
    """Runs a validated custom executable with argv arrays (never shell=True)."""

    conformance_passed = False

    def __init__(self, config: CustomHarnessConfig) -> None:
        validation = validate_custom_harness_config(config)
        if not validation.ok:
            raise ValueError(
                "; ".join(f"{item.code}: {item.message}" for item in validation.issues)
            )
        self.config = config
        self.executable_name = config.executable
        self.environment_keys = frozenset(config.environment_allowlist)

    @property
    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            harness_id=self.config.harness_id,
            display_name=self.config.display_name,
            capabilities=custom_capability_set(self.config),
            max_concurrency=1,
        )

    async def detect(self) -> HarnessDescriptor:
        readiness = assess_custom_harness_readiness(self.config)
        available = readiness.ready
        executable = readiness.checks.get("executable", {}).get("detail")
        return HarnessDescriptor(
            manifest=self.manifest,
            availability=(
                HarnessAvailability.AVAILABLE if available else HarnessAvailability.UNAVAILABLE
            ),
            executable=executable if available else None,
            support_status=(
                SupportStatus.EXPERIMENTAL if available else SupportStatus.UNAVAILABLE
            ),
            detail=None if available else "custom harness not ready",
        )

    def build_launch_spec(self, request: RunRequest) -> LaunchSpec:
        resolved = shutil.which(self.config.executable) or self.config.executable
        argv = [resolved, *self.config.args]
        if self.config.input_mode == "argv":
            argv.extend(["--task", request.task])
        if self.config.working_directory == "workspace":
            cwd = request.workspace
        else:
            cwd = os.getcwd()
        env = filter_environment(extra_keys=frozenset(self.config.environment_allowlist))
        # Never inherit unrestricted environment — only allowlisted keys via filter.
        return LaunchSpec(
            argv=tuple(argv),
            cwd=cwd,
            env=env,
            timeout_seconds=min(
                float(self.config.timeout_seconds),
                float(request.timeout_seconds or self.config.timeout_seconds),
            ),
        )

    def normalize_output(
        self,
        *,
        run_id: str,
        sequence: int,
        stream: str,
        line: str,
    ) -> AdapterObservation:
        return AdapterObservation(
            event=NormalizedEvent(
                run_id=run_id,
                sequence=sequence,
                type=EventType.HARNESS_OUTPUT,
                message=redact_secrets(line),
                payload={"stream": stream, "output_mode": self.config.output_mode},
            )
        )
