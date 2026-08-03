"""Replay-safe approval continuation for execution directives."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from joymesh.control_plane.security import (
    ExpiredMessageError,
    ReplayDetectedError,
    generate_node_keypair,
    sign_bytes,
    verify_bytes,
)
from joymesh.models import utc_now


class ExecutionApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str = Field(default_factory=lambda: str(uuid4()))
    execution_id: str
    attempt_id: str
    directive_hash: str
    reason: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    nonce: str = Field(default_factory=lambda: str(uuid4()))

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ExecutionApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    execution_id: str
    attempt_id: str
    directive_hash: str
    approved: bool
    nonce: str
    expires_at: datetime
    signature: str
    public_key: str

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def directive_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ApprovalContinuationService:
    """Bind approvals to execution+attempt+directive with expiry and replay protection."""

    def __init__(self, *, default_ttl_seconds: float = 900.0) -> None:
        self.default_ttl_seconds = max(30.0, float(default_ttl_seconds))
        self._private_key, self.public_key = generate_node_keypair()
        self._seen_nonces: dict[str, datetime] = {}
        self._pending: dict[str, ExecutionApprovalRequest] = {}

    def request_approval(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        directive_payload: dict[str, Any],
        reason: str,
        expires_at: datetime | None = None,
    ) -> ExecutionApprovalRequest:
        request = ExecutionApprovalRequest(
            execution_id=execution_id,
            attempt_id=attempt_id,
            directive_hash=directive_hash(directive_payload),
            reason=reason,
            expires_at=expires_at
            or (utc_now() + timedelta(seconds=self.default_ttl_seconds)),
        )
        self._pending[request.approval_id] = request
        return request

    def sign_response(
        self,
        request: ExecutionApprovalRequest,
        *,
        approved: bool,
    ) -> ExecutionApprovalResponse:
        if request.expires_at <= utc_now():
            raise ExpiredMessageError("approval request has expired")
        payload = {
            "approval_id": request.approval_id,
            "execution_id": request.execution_id,
            "attempt_id": request.attempt_id,
            "directive_hash": request.directive_hash,
            "approved": approved,
            "nonce": request.nonce,
            "expires_at": request.expires_at.isoformat(),
        }
        signature = sign_bytes(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            self._private_key,
        )
        return ExecutionApprovalResponse(
            approval_id=request.approval_id,
            execution_id=request.execution_id,
            attempt_id=request.attempt_id,
            directive_hash=request.directive_hash,
            approved=approved,
            nonce=request.nonce,
            expires_at=request.expires_at,
            signature=signature,
            public_key=self.public_key,
        )

    def verify_response(
        self,
        response: ExecutionApprovalResponse,
        *,
        expected_execution_id: str,
        expected_attempt_id: str,
        expected_directive_hash: str,
    ) -> None:
        if response.expires_at <= utc_now():
            raise ExpiredMessageError("approval response has expired")
        now = utc_now()
        self._seen_nonces = {
            key: expiry for key, expiry in self._seen_nonces.items() if expiry > now
        }
        if response.nonce in self._seen_nonces:
            raise ReplayDetectedError("approval nonce has already been used")
        bindings = (
            hmac.compare_digest(response.execution_id, expected_execution_id),
            hmac.compare_digest(response.attempt_id, expected_attempt_id),
            hmac.compare_digest(response.directive_hash, expected_directive_hash),
        )
        if not all(bindings):
            raise PermissionError("approval is not bound to this execution")
        payload = {
            "approval_id": response.approval_id,
            "execution_id": response.execution_id,
            "attempt_id": response.attempt_id,
            "directive_hash": response.directive_hash,
            "approved": response.approved,
            "nonce": response.nonce,
            "expires_at": response.expires_at.isoformat(),
        }
        verify_bytes(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            response.signature,
            response.public_key,
        )
        if not response.approved:
            raise PermissionError("execution was not approved")
        self._seen_nonces[response.nonce] = response.expires_at
        self._pending.pop(response.approval_id, None)
