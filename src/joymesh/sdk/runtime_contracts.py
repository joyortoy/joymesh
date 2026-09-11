"""Neutral JoyMesh runtime/execution wire contracts.

Schema owner: JoyMesh. No service/registry/persistence imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

EXECUTION_DIRECTIVE_SCHEMA = "joy.execution_directive/v1"
EXECUTION_RESULT_SCHEMA = "joy.execution_result/v1"
RUNTIME_SNAPSHOT_SCHEMA = "joy.runtime_snapshot/v1"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ExecutionDirective:
    execution_id: str
    attempt_id: str
    organisation_id: str
    selected_harness: str
    routing_decision_id: str
    authorization_reference: str
    allowed_fallbacks: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    placement_id: str | None = None
    correlation_id: str | None = None
    mission_id: str | None = None
    expires_at: str | None = None
    schema_version: int = SCHEMA_VERSION
    schema: str = EXECUTION_DIRECTIVE_SCHEMA
    source_component: str = "joycli"
    destination_component: str = "joymesh"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_fallbacks"] = list(self.allowed_fallbacks)
        payload["required_capabilities"] = list(self.required_capabilities)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionDirective:
        version = int(data.get("schema_version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported_schema_version:{version}")
        return cls(
            execution_id=str(data["execution_id"]),
            attempt_id=str(data["attempt_id"]),
            organisation_id=str(data.get("organisation_id") or data.get("organization_id") or ""),
            selected_harness=str(data["selected_harness"]),
            routing_decision_id=str(data["routing_decision_id"]),
            authorization_reference=str(data["authorization_reference"]),
            allowed_fallbacks=tuple(str(x) for x in data.get("allowed_fallbacks") or ()),
            required_capabilities=tuple(str(x) for x in data.get("required_capabilities") or ()),
            placement_id=data.get("placement_id"),
            correlation_id=data.get("correlation_id"),
            mission_id=data.get("mission_id"),
            expires_at=data.get("expires_at"),
            schema_version=version,
            schema=str(data.get("schema", EXECUTION_DIRECTIVE_SCHEMA)),
        )


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    organisation_id: str
    ok: bool
    status: str
    harness_id: str
    backend_id: str = ""
    message: str = ""
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    attempt_id: str | None = None
    correlation_id: str | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    schema: str = EXECUTION_RESULT_SCHEMA
    source_component: str = "joymesh"
    destination_component: str = "joycli"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload["evidence_refs"] = list(self.evidence_refs)
        payload["extras"] = dict(self.extras)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionResult:
        version = int(data.get("schema_version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported_schema_version:{version}")
        return cls(
            execution_id=str(data["execution_id"]),
            organisation_id=str(data.get("organisation_id") or data.get("organization_id") or ""),
            ok=bool(data.get("ok", False)),
            status=str(data.get("status") or ("ok" if data.get("ok") else "failed")),
            harness_id=str(data.get("harness_id") or ""),
            backend_id=str(data.get("backend_id") or ""),
            message=str(data.get("message") or ""),
            reason_codes=tuple(str(x) for x in data.get("reason_codes") or ()),
            evidence_refs=tuple(str(x) for x in data.get("evidence_refs") or ()),
            attempt_id=data.get("attempt_id"),
            correlation_id=data.get("correlation_id"),
            extras=dict(data.get("extras") or {}),
            schema_version=version,
            schema=str(data.get("schema", EXECUTION_RESULT_SCHEMA)),
        )


@dataclass(frozen=True)
class RuntimeSnapshot:
    snapshot_id: str
    organisation_id: str
    harnesses: tuple[Mapping[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()
    correlation_id: str | None = None
    created_at: str | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    schema: str = RUNTIME_SNAPSHOT_SCHEMA
    source_component: str = "joymesh"
    destination_component: str = "joycli"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["harnesses"] = [dict(h) for h in self.harnesses]
        payload["reason_codes"] = list(self.reason_codes)
        payload["extras"] = dict(self.extras)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeSnapshot:
        version = int(data.get("schema_version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported_schema_version:{version}")
        return cls(
            snapshot_id=str(data.get("snapshot_id") or data.get("correlation_id") or ""),
            organisation_id=str(data.get("organisation_id") or data.get("organization_id") or ""),
            harnesses=tuple(dict(h) for h in data.get("harnesses") or ()),
            reason_codes=tuple(str(x) for x in data.get("reason_codes") or ()),
            correlation_id=data.get("correlation_id"),
            created_at=data.get("created_at"),
            extras=dict(data.get("extras") or {}),
            schema_version=version,
            schema=str(data.get("schema", RUNTIME_SNAPSHOT_SCHEMA)),
        )
