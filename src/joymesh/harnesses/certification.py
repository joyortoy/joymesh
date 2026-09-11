"""Certification planning and evidence recording."""

from __future__ import annotations

import platform
from uuid import uuid4

from joymesh.harnesses.contracts import (
    CertificationEvidence,
    CertificationState,
    LifecycleAction,
    LifecyclePlan,
)
from joymesh.harnesses.registry import HarnessRegistry
from joymesh.persistence import Database

CONFORMANCE_CHECKS = (
    "installation_detection",
    "version_reporting",
    "capability_manifest",
    "launch_specification",
    "environment_filtering",
    "workspace_propagation",
    "streaming_output",
    "normalized_events",
    "event_sequence_ordering",
    "cancellation",
    "timeout_handling",
    "process_tree_cleanup",
    "successful_completion",
    "failure_propagation",
    "session_identifier_extraction",
    "session_resume",
    "usage_extraction",
    "quota_rate_limit_classification",
    "secret_redaction",
    "unsupported_feature_reporting",
)


class CertificationService:
    def __init__(self, registry: HarnessRegistry, database: Database) -> None:
        self.registry = registry
        self.database = database

    def plan(self, harness_id: str) -> LifecyclePlan:
        resolved = self.registry.resolve_id(harness_id)
        return LifecyclePlan(
            id=str(uuid4()),
            action=LifecycleAction.CERTIFY,
            harness_id=resolved,
            argv=("joymesh", "harness", "certify", resolved, "--approve"),
            notes=(
                "Certification may execute the installed harness and consume provider quota.",
                "A disposable workspace and explicit approval are required.",
            ),
        )

    async def record(
        self,
        *,
        harness_id: str,
        binary_version: str | None,
        executable: str | None,
        checks: dict[str, bool],
        detail: str | None = None,
    ) -> CertificationEvidence:
        resolved = self.registry.resolve_id(harness_id)
        state = (
            CertificationState.BINARY_CERTIFIED
            if checks and all(checks.values())
            else CertificationState.FAILED
        )
        evidence = CertificationEvidence(
            id=str(uuid4()),
            harness_id=resolved,
            adapter_version=self.registry.get(resolved).manifest.adapter_version,
            binary_version=binary_version,
            executable=executable,
            state=state,
            checks=checks,
            detail=detail,
            operating_system=platform.platform(),
        )
        return await self.database.record_certification(evidence)
