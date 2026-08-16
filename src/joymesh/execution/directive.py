"""Canonical execution directive — JoyCLI decides; JoyMesh validates and executes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from joymesh.models import Capability


class ExecutionDirective(BaseModel):
    """Authoritative launch directive produced by JoyCLI routing."""

    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    selected_harness: str = Field(min_length=1)
    allowed_fallbacks: tuple[str, ...] = ()
    required_capabilities: frozenset[Capability] = Field(default_factory=frozenset)
    routing_decision_id: str = Field(min_length=1)
    runtime_projection_revision: str = Field(min_length=1)
    authorization_reference: str = Field(min_length=1)
    expires_at: datetime
    fallback_authorization_references: frozenset[str] = Field(default_factory=frozenset)

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "selected_harness": self.selected_harness,
            "allowed_fallbacks": list(self.allowed_fallbacks),
            "required_capabilities": sorted(
                capability.value for capability in self.required_capabilities
            ),
            "routing_decision_id": self.routing_decision_id,
            "runtime_projection_revision": self.runtime_projection_revision,
            "authorization_reference": self.authorization_reference,
            "expires_at": self.expires_at.isoformat(),
            "fallback_authorization_references": sorted(
                self.fallback_authorization_references
            ),
        }
