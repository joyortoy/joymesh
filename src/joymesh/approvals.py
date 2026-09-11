"""Generic approval primitives for consuming applications."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ApprovalRisk(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    summary: str
    risk: ApprovalRisk
    requires_confirmation: bool = True
