"""Generic connector runtime protocol — connector-specific details stay in adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from joymesh.models import utc_now


@dataclass(frozen=True)
class ConnectorExecutionContext:
    node_id: str
    workspace_id: str | None = None
    workspace_path: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    lease_id: str | None = None
    fencing_token: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryResult:
    """Connector-owned discovery outcome consumed by generic readiness/runtime code."""

    executable_path: str | None
    version: str | None
    fingerprint: str | None
    installed: bool = False
    usable: bool = False
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def executable(self) -> str | None:
        return self.executable_path


@dataclass(frozen=True)
class AuthenticationResult:
    authenticated: bool
    method_id: str
    detail: str
    version: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True)
class ConnectorPlan:
    plan_id: str
    connector_id: str
    connector_revision: str
    action: str
    executable: str
    arguments: tuple[str, ...]
    plan_hash: str
    expires_at: datetime
    risk_level: str = "medium"


@dataclass(frozen=True)
class AuthenticationEvidence:
    status: str
    method_id: str
    executable_path: str | None
    fingerprint: str | None
    version: str | None
    details: Mapping[str, Any]


@dataclass(frozen=True)
class AdapterVerificationResult:
    passed: bool
    executable_path: str | None
    fingerprint: str | None
    version: str | None
    details: Mapping[str, Any]


@dataclass(frozen=True)
class ConnectorRunRequest:
    execution_id: str
    prompt: str
    capabilities: frozenset[str]
    timeout_seconds: int
    workspace_path: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessEvent:
    event_type: str
    sequence: int
    payload: Mapping[str, Any]
    created_at: datetime = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class CancellationResult:
    cancelled: bool
    lingering: bool
    detail: str | None = None


@dataclass(frozen=True)
class CertificationProfileDefinition:
    profile_id: str
    profile_revision: str
    required_capabilities: frozenset[str]
    description: str


@dataclass(frozen=True)
class ConnectorRuntimeNotice:
    """Connector-neutral structured notice for browser/API surfaces."""

    event_type: str
    connector_id: str
    reason_code: str
    message: str
    recoverable: bool
    recommended_action: str | None = None
    display_name: str | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "connector_id": self.connector_id,
            "display_name": self.display_name,
            "reason_code": self.reason_code,
            "message": self.message,
            "recoverable": self.recoverable,
            "recommended_action": self.recommended_action,
        }


@dataclass(frozen=True)
class ConnectorRuntimeEvent:
    """Connector-neutral parsed execution event."""

    event_type: str
    sequence: int
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorLiveTestResult:
    """Result of a connector-neutral local live test."""

    connector_id: str
    display_name: str
    installed: bool
    usable: bool
    authenticated: bool | None
    discovery_reason_code: str | None
    executable_path: str | None
    version: str | None
    exit_code: int | None
    duration_ms: int
    certification_passed: bool
    notices: tuple[ConnectorRuntimeNotice, ...]
    events: tuple[ConnectorRuntimeEvent, ...]
    error: str | None = None
    auth_status: str | None = None
    fingerprint: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "connector_id": self.connector_id,
            "display_name": self.display_name,
            "installed": self.installed,
            "usable": self.usable,
            "authenticated": self.authenticated,
            "auth_status": self.auth_status,
            "discovery_reason_code": self.discovery_reason_code,
            "executable_path": self.executable_path,
            "version": self.version,
            "fingerprint": self.fingerprint,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "certification_passed": self.certification_passed,
            "notices": [item.as_payload() for item in self.notices],
            "events": [
                {
                    "event_type": item.event_type,
                    "sequence": item.sequence,
                    "payload": dict(item.payload),
                }
                for item in self.events
            ],
            "error": self.error,
        }


@runtime_checkable
class ConnectorRuntime(Protocol):
    connector_id: str
    display_name: str
    connector_revision: str

    def declared_capabilities(self) -> frozenset[str]: ...

    def certification_profiles(self) -> tuple[CertificationProfileDefinition, ...]: ...

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult: ...

    def classify_auth_status(self, output: str, *, returncode: int) -> str: ...

    async def inspect_authentication(
        self, context: ConnectorExecutionContext
    ) -> AuthenticationResult: ...

    async def build_authentication_plan(
        self, context: ConnectorExecutionContext
    ) -> ConnectorPlan: ...

    async def verify_authentication(
        self, context: ConnectorExecutionContext
    ) -> AuthenticationEvidence: ...

    async def verify_adapter(
        self, context: ConnectorExecutionContext
    ) -> AdapterVerificationResult: ...

    def adapter_verification_notice(self) -> ConnectorRuntimeNotice | None: ...

    def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]: ...

    def build_exec_argv(
        self,
        *,
        executable: str,
        prompt: str,
        workspace_path: str,
        read_only: bool = True,
    ) -> Sequence[str]: ...

    def build_read_only_cert_argv(
        self,
        *,
        executable: str,
        prompt: str,
        workspace: Path,
    ) -> Sequence[str]: ...

    def parse_events(self, output: str) -> Sequence[Mapping[str, Any]]: ...

    def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]: ...

    async def cancel(
        self, execution_id: str, context: ConnectorExecutionContext
    ) -> CancellationResult: ...
