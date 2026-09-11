"""ExecutionCompletionOrchestrator — authority, evidence, verification, recovery."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from joymesh.runtime_v1.completion import (
    CandidateEvidence,
    CompletionContext,
    CompletionFailureClass,
    CompletionLifecycleState,
    EvidenceBoundary,
    EvidenceRejectionReason,
    EvidenceTrustClassification,
    ExecutionCompletionOrchestrator,
    UsageFact,
    UsageFinaliser,
    VerificationOutcome,
    VerificationRequest,
    VerificationService,
)
from joymesh.runtime_v1.execution_routing.models import ExecutionResult, ExecutionStatus
from joymesh.security import filter_environment, redact_secrets


def _ctx(
    *,
    execution_id: str = "exec-1",
    attempt_id: str = "att-1",
    authoritative: str | None = None,
    org: str | None = "org-1",
    project: str | None = "proj-1",
    mission_id: str = "mission-1",
    strategy: str = "backend_success_with_evidence",
    require_evidence: bool = False,
    required: tuple[str, ...] = (),
    cancelled: bool = False,
    metadata: dict | None = None,
) -> CompletionContext:
    return CompletionContext(
        organisation_id=org,
        project_id=project,
        mission_id=mission_id,
        execution_id=execution_id,
        attempt_id=attempt_id,
        authoritative_attempt_id=authoritative or attempt_id,
        backend_id="local",
        harness_id="cursor",
        require_evidence=require_evidence,
        verification_strategy=strategy,
        required_evidence_types=required,
        cancelled=cancelled,
        metadata=metadata or {},
    )


def _backend_ok(**kwargs) -> ExecutionResult:
    defaults = {
        "ok": True,
        "execution_id": "exec-1",
        "backend_id": "local",
        "harness_id": "cursor",
        "status": ExecutionStatus.SUCCEEDED,
        "message": "backend exited 0",
        "usage": {"wall_clock_seconds": 1.5, "input_tokens": 10, "output_tokens": 5},
    }
    defaults.update(kwargs)
    return ExecutionResult(**defaults)


@pytest.mark.asyncio
async def test_backend_success_does_not_complete_without_verification() -> None:
    orch = ExecutionCompletionOrchestrator()
    result = _backend_ok()
    # Force verification failure via required missing evidence.
    outcome = await orch.complete_from_backend(
        result,
        context=_ctx(require_evidence=True, required=("test_summary",)),
    )
    assert outcome.ok is False
    assert outcome.state is CompletionLifecycleState.FAILED
    assert outcome.failure_class == CompletionFailureClass.EVIDENCE_MISSING.value


@pytest.mark.asyncio
async def test_mission_completes_only_after_verification_passes() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(_backend_ok(), context=_ctx())
    assert outcome.ok is True
    assert outcome.state is CompletionLifecycleState.COMPLETED
    types = [e["event_type"] for e in outcome.events]
    assert "backend.completed" in types
    assert "verification.passed" in types
    assert "execution.completed" in types


@pytest.mark.asyncio
async def test_verification_failure_prevents_completion() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(
        _backend_ok(),
        context=_ctx(
            strategy="test_command",
            require_evidence=True,
            required=("test_summary",),
        ),
        candidate_evidence=[
            CandidateEvidence(
                evidence_type="test_summary",
                sequence=1,
                provenance={"execution_id": "exec-1"},
                payload={"passed": False},
            )
        ],
    )
    assert outcome.ok is False
    assert outcome.state is CompletionLifecycleState.FAILED
    assert "verification.failed" in [e["event_type"] for e in outcome.events]


@pytest.mark.asyncio
async def test_inconclusive_verification_non_successful() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(
        _backend_ok(),
        context=_ctx(strategy="content_hash_match", metadata={"verification_policy": {}}),
    )
    assert outcome.ok is False
    assert outcome.state is CompletionLifecycleState.BLOCKED


@pytest.mark.asyncio
async def test_backend_cannot_override_failed_verification() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(
        _backend_ok(candidate_verification={"passed": True, "outcome": "verified"}),
        context=_ctx(strategy="test_command"),
        candidate_evidence=[
            CandidateEvidence(
                evidence_type="test_summary",
                sequence=1,
                provenance={"execution_id": "exec-1"},
                payload={"passed": False},
            )
        ],
    )
    assert outcome.ok is False


@pytest.mark.asyncio
async def test_restore_failure_blocks_completion() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(
        _backend_ok(
            failure_class="provider_restore_failure",
            restore_status="failed",
            output={"restored": False},
        ),
        context=_ctx(),
    )
    assert outcome.state is CompletionLifecycleState.BLOCKED
    assert outcome.failure_class == CompletionFailureClass.RESTORE_FAILED.value


@pytest.mark.asyncio
async def test_stale_attempt_rejected() -> None:
    orch = ExecutionCompletionOrchestrator()
    first = await orch.complete_from_backend(
        _backend_ok(), context=_ctx(attempt_id="att-2", authoritative="att-2")
    )
    assert first.ok is True
    late = await orch.complete_from_backend(
        _backend_ok(), context=_ctx(attempt_id="att-1", authoritative="att-2")
    )
    assert late.ok is True  # authoritative already completed
    assert (
        late.failure_class in {None, CompletionFailureClass.STALE_ATTEMPT.value}
        or late.state is CompletionLifecycleState.COMPLETED
    )
    # Stale before terminal:
    orch2 = ExecutionCompletionOrchestrator()
    rejected = await orch2.complete_from_backend(
        _backend_ok(), context=_ctx(attempt_id="att-1", authoritative="att-2")
    )
    assert rejected.ok is False
    assert rejected.failure_class == CompletionFailureClass.STALE_ATTEMPT.value


@pytest.mark.asyncio
async def test_superseded_remote_attempt_rejected() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_remote_event(
        context=_ctx(attempt_id="old", authoritative="new"),
        event_type="task.succeeded",
        payload={"verification": {"outcome": "verified", "passed": True}},
    )
    assert outcome.ok is False
    assert outcome.failure_class == CompletionFailureClass.SUPERSEDED_ATTEMPT.value


@pytest.mark.asyncio
async def test_remote_success_without_evidence_fails() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_remote_event(
        context=_ctx(
            strategy="remote_verifier_event",
            require_evidence=True,
            required=("remote_verification",),
        ),
        event_type="task.succeeded",
        payload={},
    )
    assert outcome.ok is False
    assert outcome.state is CompletionLifecycleState.FAILED


@pytest.mark.asyncio
async def test_remote_verified_completion() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_remote_event(
        context=_ctx(
            strategy="remote_verifier_event",
            require_evidence=True,
            required=("remote_verification",),
        ),
        event_type="task.succeeded",
        payload={"verification": {"outcome": "verified", "passed": True}, "sequence": 1},
    )
    assert outcome.ok is True
    assert outcome.state is CompletionLifecycleState.COMPLETED


@pytest.mark.asyncio
async def test_duplicate_completion_idempotent_usage() -> None:
    orch = ExecutionCompletionOrchestrator()
    ctx = _ctx()
    first = await orch.complete_from_backend(_backend_ok(), context=ctx)
    second = await orch.complete_from_backend(_backend_ok(), context=ctx)
    assert first.ok and second.ok
    assert first.usage.get("attempt_count") == second.usage.get("attempt_count")
    assert orch.usage.is_finalised("exec-1")


@pytest.mark.asyncio
async def test_cancellation_finalisation() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.finalise_cancelled(_ctx(cancelled=True), cleanup_completed=True)
    assert outcome.state is CompletionLifecycleState.CANCELLED
    again = await orch.finalise_cancelled(_ctx(cancelled=True), cleanup_completed=True)
    assert again.state is CompletionLifecycleState.CANCELLED
    incomplete = await ExecutionCompletionOrchestrator().finalise_cancelled(
        _ctx(execution_id="exec-2", cancelled=True), cleanup_completed=False
    )
    assert incomplete.state is CompletionLifecycleState.BLOCKED


@pytest.mark.asyncio
async def test_resume_finalising_and_verification_pending() -> None:
    orch = ExecutionCompletionOrchestrator()
    record = orch.store.get("missing")
    assert record is None
    # Seed a stuck verification_pending record.
    from joymesh.runtime_v1.completion.models import CompletionRecord

    stuck = CompletionRecord(
        execution_id="exec-resume",
        mission_id="mission-1",
        state=CompletionLifecycleState.VERIFICATION_PENDING,
        attempt_id="att-1",
        authoritative_attempt_id="att-1",
        backend_id="local",
        harness_id="cursor",
        organisation_id="org-1",
        project_id="proj-1",
        backend_ok=True,
        restore_ok=True,
    )
    orch.store.save(stuck)
    resumed = await orch.resume("exec-resume")
    assert resumed is not None
    assert resumed.state in {
        CompletionLifecycleState.COMPLETED,
        CompletionLifecycleState.FAILED,
        CompletionLifecycleState.BLOCKED,
    }


def test_evidence_tenant_and_identity_rejection() -> None:
    boundary = EvidenceBoundary()
    ctx = _ctx()
    accepted, rejected = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=1,
                provenance={"organisation_id": "other-org", "execution_id": "exec-1"},
                payload={"x": 1},
            )
        ],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert not accepted
    assert rejected[0].rejection_reason is EvidenceRejectionReason.WRONG_TENANT


def test_evidence_wrong_execution_and_attempt() -> None:
    boundary = EvidenceBoundary()
    ctx = _ctx()
    _, rejected = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=1,
                provenance={"execution_id": "wrong"},
                payload={"x": 1},
            )
        ],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert rejected[0].rejection_reason is EvidenceRejectionReason.WRONG_EXECUTION
    _, rejected2 = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=1,
                provenance={"execution_id": "exec-1", "attempt_id": "wrong"},
                payload={"x": 1},
            )
        ],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert rejected2[0].rejection_reason is EvidenceRejectionReason.WRONG_ATTEMPT


def test_evidence_sequence_duplicate_oversized_idempotent() -> None:
    boundary = EvidenceBoundary(max_bytes=100)
    ctx = _ctx(metadata={})
    ctx = CompletionContext(
        organisation_id="org-1",
        project_id="proj-1",
        mission_id="mission-1",
        execution_id="exec-1",
        attempt_id="att-1",
        authoritative_attempt_id="att-1",
        backend_id="local",
        harness_id="cursor",
        max_evidence_bytes=50,
    )
    cand = CandidateEvidence(
        evidence_type="backend_output",
        sequence=2,
        provenance={"execution_id": "exec-1"},
        payload={"ok": True},
        size_bytes=10,
    )
    accepted, _ = boundary.intake(
        [cand],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert len(accepted) == 1
    # Idempotent
    accepted2, rejected2 = boundary.intake(
        [cand],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert accepted2 and not rejected2
    assert len(boundary.list_all("exec-1")) == 1
    # Invalid sequence
    _, rejected_seq = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=1,
                provenance={"execution_id": "exec-1"},
                payload={"later": False},
                size_bytes=5,
            )
        ],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert rejected_seq[0].rejection_reason is EvidenceRejectionReason.INVALID_SEQUENCE
    # Conflicting duplicate
    _, rejected_conflict = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=2,
                provenance={"execution_id": "exec-1"},
                payload={"ok": False},
                size_bytes=5,
            )
        ],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert rejected_conflict[0].rejection_reason is EvidenceRejectionReason.DUPLICATE_CONFLICTING
    # Oversized
    _, rejected_size = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=3,
                provenance={"execution_id": "exec-1"},
                payload={"big": True},
                size_bytes=500,
            )
        ],
        context=ctx,
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert rejected_size[0].rejection_reason is EvidenceRejectionReason.OVERSIZED


def test_evidence_secrets_redacted() -> None:
    boundary = EvidenceBoundary()
    accepted, _ = boundary.intake(
        [
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=1,
                provenance={"execution_id": "exec-1"},
                payload={"detail": "api_key=sk-abcdefghijklmnopqrstuvwxyz"},
            )
        ],
        context=_ctx(),
        lifecycle_state=CompletionLifecycleState.EVIDENCE_PENDING,
        source_backend="local",
        source_harness="cursor",
    )
    assert "[REDACTED]" in accepted[0].payload["detail"]


def test_verification_strategies() -> None:
    svc = VerificationService()
    from joymesh.runtime_v1.completion.models import EvidenceEnvelope

    def _ev(etype: str, payload: dict, **kwargs) -> EvidenceEnvelope:
        return EvidenceEnvelope(
            evidence_id="e1",
            execution_id="exec-1",
            attempt_id="att-1",
            mission_id="m1",
            project_id="p1",
            organisation_id="o1",
            evidence_type=etype,
            source_backend="local",
            source_harness="cursor",
            content_ref=kwargs.get("content_ref"),
            content_hash=kwargs.get("content_hash"),
            size_bytes=1,
            sequence=1,
            trust=EvidenceTrustClassification.ACCEPTED,
            producer_identity="local",
            payload=payload,
        )

    assert (
        svc.verify(
            VerificationRequest(
                mission_id="m",
                execution_id="e",
                attempt_id="a",
                strategy="test_command",
                accepted_evidence=(_ev("test_summary", {"passed": True}),),
                backend_ok=True,
            )
        ).outcome
        is VerificationOutcome.VERIFIED
    )
    assert (
        svc.verify(
            VerificationRequest(
                mission_id="m",
                execution_id="e",
                attempt_id="a",
                strategy="artifact_existence",
                policy={"required_artifacts": ("dist/app",)},
                accepted_evidence=(_ev("artifact", {"artifact": "dist/app"}),),
                backend_ok=True,
            )
        ).outcome
        is VerificationOutcome.VERIFIED
    )
    missing = svc.verify(
        VerificationRequest(
            mission_id="m",
            execution_id="e",
            attempt_id="a",
            strategy="artifact_existence",
            policy={"required_artifacts": ("dist/app",)},
            accepted_evidence=(),
            backend_ok=True,
        )
    )
    assert missing.outcome is VerificationOutcome.FAILED
    timeout = svc.verify(
        VerificationRequest(
            mission_id="m",
            execution_id="e",
            attempt_id="a",
            strategy="always_pass",
            policy={"force_timeout": True},
            backend_ok=True,
        )
    )
    assert timeout.failure_class is CompletionFailureClass.VERIFICATION_TIMEOUT
    err = svc.verify(
        VerificationRequest(
            mission_id="m",
            execution_id="e",
            attempt_id="a",
            strategy="always_pass",
            policy={"force_error": True},
            backend_ok=True,
        )
    )
    assert err.failure_class is CompletionFailureClass.VERIFICATION_ERROR
    human = svc.verify(
        VerificationRequest(
            mission_id="m",
            execution_id="e",
            attempt_id="a",
            strategy="always_pass",
            policy={"require_human_approval": True},
            backend_ok=True,
        )
    )
    assert human.outcome is VerificationOutcome.PENDING_HUMAN_APPROVAL
    composite = svc.verify(
        VerificationRequest(
            mission_id="m",
            execution_id="e",
            attempt_id="a",
            strategy="composite",
            policy={"strategies": ["backend_success_with_evidence", "always_pass"]},
            backend_ok=True,
        )
    )
    assert composite.outcome is VerificationOutcome.VERIFIED


def test_usage_finalisation_tenant_and_idempotency() -> None:
    usage = UsageFinaliser()
    fact = UsageFact(
        organisation_id="org-1",
        project_id="p",
        mission_id="m",
        execution_id="e1",
        attempt_id="a1",
        backend_id="local",
        harness_id="cursor",
        facts={"wall_clock_seconds": 2, "input_tokens": 3, "output_tokens": 4},
    )
    agg1 = usage.finalise(
        execution_id="e1",
        organisation_id="org-1",
        project_id="p",
        mission_id="m",
        attempt_facts=(fact,),
    )
    agg2 = usage.finalise(
        execution_id="e1",
        organisation_id="org-1",
        project_id="p",
        mission_id="m",
        attempt_facts=(fact,),
    )
    assert agg1 == agg2
    with pytest.raises(PermissionError):
        usage.record_attempt(
            UsageFact(
                organisation_id="org-other",
                project_id="p",
                mission_id="m",
                execution_id="e1",
                attempt_id="a2",
                backend_id="local",
                harness_id="cursor",
                facts={"wall_clock_seconds": 1},
            )
        )


def test_environment_hardening_secrets_not_inherited() -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/tmp",
        "XAI_API_KEY": "secret-value",
        "ANTHROPIC_API_KEY": "secret-value",
        "CURSOR_API_KEY": "secret-value",
        "OPENAI_API_KEY": "secret-value",
        "MY_CUSTOM_TOKEN": "nope",
        "SAFE_FLAG": "ok",
    }
    filtered = filter_environment(source, extra_keys=frozenset({"SAFE_FLAG", "XAI_API_KEY"}))
    assert "PATH" in filtered
    assert "SAFE_FLAG" in filtered
    assert "XAI_API_KEY" not in filtered
    assert "ANTHROPIC_API_KEY" not in filtered
    assert "MY_CUSTOM_TOKEN" not in filtered
    assert "[REDACTED]" in redact_secrets("token=supersecretvalue123")


def test_cursor_grok_probe_and_execute_use_filter_environment() -> None:
    root = Path(__file__).resolve().parents[1] / "src/joymesh/runtime_v1/connectors"
    for name in ("cursor.py", "grok.py", "claude.py", "codex.py", "opencode.py"):
        text = (root / name).read_text()
        assert "filter_environment" in text
        # Every create_subprocess_exec should pass env=
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name_attr = getattr(func, "attr", None)
                if name_attr == "create_subprocess_exec":
                    assert any(kw.arg == "env" for kw in node.keywords), (
                        f"{name} create_subprocess_exec missing env= near line {node.lineno}"
                    )


def test_architecture_backends_no_mission_completion() -> None:
    root = Path(__file__).resolve().parents[1] / "src/joymesh/runtime_v1"
    forbidden = (
        "RuntimeTaskStatus.SUCCEEDED",
        "mission_completed",
        "complete_mission",
        "mark_mission_complete",
    )
    backend_dir = root / "execution_routing" / "backends"
    for path in backend_dir.glob("*.py"):
        text = path.read_text()
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"
    for path in (root / "connectors").glob("*.py"):
        text = path.read_text()
        assert "VerificationService" not in text
        assert "ExecutionCompletionOrchestrator" not in text or path.name == "__init__.py"


def test_architecture_runtime_uses_completion_orchestrator() -> None:
    text = (Path(__file__).resolve().parents[1] / "src/joymesh/runtime_v1/service.py").read_text()
    assert "ExecutionCompletionOrchestrator" in text
    assert "complete_from_backend" in text
    assert "complete_from_remote_event" in text


def test_architecture_no_second_verification_loop_in_backends() -> None:
    backend_dir = (
        Path(__file__).resolve().parents[1] / "src/joymesh/runtime_v1/execution_routing/backends"
    )
    for path in backend_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {
                "VerificationService",
                "ExecutionCompletionOrchestrator",
                "UsageFinaliser",
            }:
                raise AssertionError(f"{path.name} references {node.id}")


@pytest.mark.asyncio
async def test_failed_verification_still_records_usage() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(
        _backend_ok(),
        context=_ctx(require_evidence=True, required=("missing_type",)),
    )
    assert outcome.ok is False
    assert outcome.usage.get("finalised") is True
    assert orch.usage.is_finalised("exec-1")


@pytest.mark.asyncio
async def test_human_approval_remains_blocked() -> None:
    orch = ExecutionCompletionOrchestrator()
    outcome = await orch.complete_from_backend(
        _backend_ok(),
        context=_ctx(metadata={"verification_policy": {"require_human_approval": True}}),
    )
    assert outcome.state is CompletionLifecycleState.BLOCKED
