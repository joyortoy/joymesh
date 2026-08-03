"""Validation for runtime snapshots (reject invalid facts, never invent policy)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from joymesh.quota.contracts import HarnessAvailability, QuotaState
from joymesh.runtime_snapshot.contracts import (
    SCHEMA_VERSION,
    HarnessRuntimeSnapshot,
    RuntimeSnapshot,
)


class RuntimeSnapshotValidationError(ValueError):
    """Raised when a snapshot violates the factual contract."""


_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "cookie",
        "cookies",
        "session",
        "session_secret",
        "password",
        "secret",
        "credential",
        "credentials",
        "authorization",
        "user_id",
        "userid",
        "user",
        "email",
        "subscription",
        "subscription_id",
        "subscription_name",
        "billing",
        "prompt",
        "conversation",
        "repository",
        "workspace",
        "terminal_output",
        "source_code",
        "checkpoint",
    }
)


def sanitize_provider_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Drop secret / identity / content keys; keep factual provider fields only."""

    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower().replace("-", "_")
        if lowered in _FORBIDDEN_METADATA_KEYS:
            continue
        if any(part in lowered for part in ("token", "secret", "password", "cookie", "api_key")):
            continue
        if isinstance(value, Mapping):
            cleaned[key] = sanitize_provider_metadata(value)
        else:
            cleaned[key] = value
    return cleaned


def validate_harness_entry(entry: HarnessRuntimeSnapshot) -> None:
    if not entry.harness_id:
        raise RuntimeSnapshotValidationError("harness_id must be non-empty")
    if entry.quota.harness_id != entry.harness_id:
        raise RuntimeSnapshotValidationError(
            f"quota harness_id mismatch for {entry.harness_id}"
        )
    if not isinstance(entry.availability, HarnessAvailability):
        raise RuntimeSnapshotValidationError("invalid availability")
    if not isinstance(entry.quota.state, QuotaState):
        raise RuntimeSnapshotValidationError("invalid quota state")
    # Auth/config consistency: authentication_required implies not authenticated.
    if (
        entry.availability is HarnessAvailability.AUTHENTICATION_REQUIRED
        and entry.authenticated
    ):
        raise RuntimeSnapshotValidationError(
            "authentication_required cannot be authenticated=true"
        )
    if (
        entry.availability is HarnessAvailability.CONFIGURATION_REQUIRED
        and entry.configured
    ):
        raise RuntimeSnapshotValidationError(
            "configuration_required cannot be configured=true"
        )
    usage = entry.recent_usage
    for name, value in (
        ("input_tokens", usage.input_tokens),
        ("output_tokens", usage.output_tokens),
        ("total_tokens", usage.total_tokens),
        ("execution_count", usage.execution_count),
    ):
        if value < 0:
            raise RuntimeSnapshotValidationError(f"{name} must not be negative")
    if usage.average_duration_ms is not None and usage.average_duration_ms < 0:
        raise RuntimeSnapshotValidationError("average_duration_ms must not be negative")
    latency = entry.latency
    for latency_name, latency_value in (
        ("average_ms", latency.average_ms),
        ("last_ms", latency.last_ms),
        ("p95_ms", latency.p95_ms),
    ):
        if latency_value is not None and latency_value < 0:
            raise RuntimeSnapshotValidationError(
                f"{latency_name} must not be negative"
            )


def validate_snapshot(snapshot: RuntimeSnapshot) -> None:
    if snapshot.schema_version != SCHEMA_VERSION:
        raise RuntimeSnapshotValidationError(
            f"missing or unsupported schema_version: {snapshot.schema_version}"
        )
    if not snapshot.snapshot_id:
        raise RuntimeSnapshotValidationError("snapshot_id must be non-empty")
    seen: set[str] = set()
    for entry in snapshot.harnesses:
        if entry.harness_id in seen:
            raise RuntimeSnapshotValidationError(
                f"duplicate harness id: {entry.harness_id}"
            )
        seen.add(entry.harness_id)
        validate_harness_entry(entry)


def assert_privacy(payload: Mapping[str, Any]) -> None:
    """Fail tests/callers if forbidden keys leak into a serialized snapshot."""

    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                lowered = str(key).lower().replace("-", "_")
                if lowered in _FORBIDDEN_METADATA_KEYS:
                    raise RuntimeSnapshotValidationError(
                        f"privacy violation: forbidden key {key!r}"
                    )
                stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
