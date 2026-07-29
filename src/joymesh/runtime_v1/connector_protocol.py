"""Generic connector runtime protocol — Cursor-specific details stay in adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
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
    executable_path: str | None
    version: str | None
    fingerprint: str | None
    details: Mapping[str, Any] = field(default_factory=dict)


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


class ConnectorRuntime(Protocol):
    connector_id: str
    connector_revision: str

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult: ...

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

    def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]: ...

    async def cancel(
        self, execution_id: str, context: ConnectorExecutionContext
    ) -> CancellationResult: ...

    def certification_profiles(self) -> tuple[CertificationProfileDefinition, ...]: ...

    def declared_capabilities(self) -> frozenset[str]: ...
