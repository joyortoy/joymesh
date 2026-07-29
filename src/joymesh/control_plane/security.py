"""Standard cryptographic and authorization primitives for remote operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel

from joymesh.control_plane.contracts import (
    ActionPlan,
    ApprovalDecision,
    ConnectorTaskEnvelope,
    RemoteTaskEnvelope,
)
from joymesh.models import utc_now


class ReplayDetectedError(PermissionError):
    pass


class ExpiredMessageError(PermissionError):
    pass


def canonical_json(value: BaseModel | dict[str, Any], *, exclude: set[str] | None = None) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    for field in exclude or set():
        data.pop(field, None)
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def sha256_digest(value: BaseModel | dict[str, Any], *, exclude: set[str] | None = None) -> str:
    return hashlib.sha256(canonical_json(value, exclude=exclude)).hexdigest()


def generate_node_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _encode(private_bytes), _encode(public_bytes)


def sign_envelope(envelope: RemoteTaskEnvelope, private_key: str) -> RemoteTaskEnvelope:
    signature = Ed25519PrivateKey.from_private_bytes(_decode(private_key)).sign(
        canonical_json(envelope, exclude={"signature"})
    )
    return envelope.model_copy(update={"signature": _encode(signature)})


def verify_envelope(envelope: RemoteTaskEnvelope, public_key: str) -> None:
    if envelope.expires_at <= utc_now():
        raise ExpiredMessageError("remote task envelope has expired")
    Ed25519PublicKey.from_public_bytes(_decode(public_key)).verify(
        _decode(envelope.signature),
        canonical_json(envelope, exclude={"signature"}),
    )


def bind_plan(plan: ActionPlan) -> ActionPlan:
    return plan.model_copy(update={"plan_hash": sha256_digest(plan, exclude={"plan_hash"})})


def verify_approval(plan: ActionPlan, decision: ApprovalDecision) -> None:
    if plan.expires_at <= utc_now() or decision.expires_at <= utc_now():
        raise ExpiredMessageError("plan or approval has expired")
    if not decision.approved:
        raise PermissionError("action was not approved")
    expected = sha256_digest(plan, exclude={"plan_hash"})
    if not hmac.compare_digest(plan.plan_hash, expected):
        raise PermissionError("plan hash is invalid")
    bindings = (
        decision.plan_id == plan.id,
        hmac.compare_digest(decision.plan_hash, plan.plan_hash),
        decision.user_id == plan.user_id,
        decision.browser_session_id == plan.browser_session_id,
        decision.node_id == plan.node_id,
    )
    if not all(bindings):
        raise PermissionError("approval is not bound to this exact plan")


class NonceStore:
    """Bounded replay detector; production gateways persist these records."""

    def __init__(self) -> None:
        self._seen: dict[str, datetime] = {}

    def consume(self, nonce: str, expires_at: datetime) -> None:
        now = utc_now()
        self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
        if nonce in self._seen:
            raise ReplayDetectedError("message nonce has already been used")
        self._seen[nonce] = expires_at


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def verify_pkce(verifier: str, challenge: str) -> bool:
    expected = _encode(hashlib.sha256(verifier.encode()).digest())
    return hmac.compare_digest(expected, challenge)


def resolve_workspace_path(root: str, candidate: str) -> Path:
    root_path = Path(root).expanduser().resolve(strict=True)
    requested = (root_path / candidate).resolve(strict=False)
    if requested != root_path and root_path not in requested.parents:
        raise PermissionError("path escapes the granted workspace root")
    existing = requested
    while not existing.exists() and existing != root_path:
        existing = existing.parent
    resolved_existing = existing.resolve(strict=True)
    if resolved_existing != root_path and root_path not in resolved_existing.parents:
        raise PermissionError("symlink escapes the granted workspace root")
    return requested


def sign_bytes(message: bytes, private_key: str) -> str:
    signature = Ed25519PrivateKey.from_private_bytes(_decode(private_key)).sign(message)
    return _encode(signature)


def verify_bytes(message: bytes, signature: str, public_key: str) -> None:
    Ed25519PublicKey.from_public_bytes(_decode(public_key)).verify(_decode(signature), message)


def sign_connector_envelope(
    envelope: ConnectorTaskEnvelope, private_key: str
) -> ConnectorTaskEnvelope:
    signature = Ed25519PrivateKey.from_private_bytes(_decode(private_key)).sign(
        canonical_json(envelope, exclude={"signature"})
    )
    return envelope.model_copy(update={"signature": _encode(signature)})


def verify_connector_envelope(envelope: ConnectorTaskEnvelope, public_key: str) -> None:
    if envelope.expires_at <= utc_now():
        raise ExpiredMessageError("connector task envelope has expired")
    Ed25519PublicKey.from_public_bytes(_decode(public_key)).verify(
        _decode(envelope.signature),
        canonical_json(envelope, exclude={"signature"}),
    )


def load_private_key(path: Path) -> str:
    return path.expanduser().read_text(encoding="utf-8").strip()


def store_private_key(path: Path, private_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(private_key)


def production_mode() -> bool:
    return os.environ.get("JOYMESH_ENV", "development").lower() in {
        "production",
        "prod",
    }


def inline_connector_node_enabled() -> bool:
    configured = os.environ.get("JOYMESH_INLINE_CONNECTOR_NODE")
    if configured is None:
        return not production_mode()
    enabled = configured == "1"
    if enabled and production_mode():
        raise RuntimeError("inline connector node execution is refused in production")
    return enabled


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
