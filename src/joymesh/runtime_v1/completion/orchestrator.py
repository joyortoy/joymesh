"""ExecutionCompletionOrchestrator — JoyMesh factual execution bookkeeping.

DEPRECATED FOR JOYCLI MISSION AUTHORITY
--------------------------------------
JoyCLI owns authoritative mission completion, evidence acceptance, and
verification. JoyMesh retains this module only for neutral worker/runtime
lifecycle bookkeeping. Do not treat JoyMesh CompletionOutcome as mission
terminal authority. Migration stage: joycli-completion-authority-v1.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.runtime_v1.completion.evidence import EvidenceBoundary
from joymesh.runtime_v1.completion.graph import MissionGraphProjector
from joymesh.runtime_v1.completion.models import (
    CandidateEvidence,
    CompletionContext,
    CompletionOutcome,
    CompletionRecord,
    UsageFact,
    VerificationRequest,
)
from joymesh.runtime_v1.completion.states import (
    TERMINAL_COMPLETION_STATES,
    CompletionFailureClass,
    CompletionLifecycleState,
    VerificationOutcome,
)
from joymesh.runtime_v1.completion.store import CompletionStore
from joymesh.runtime_v1.completion.usage import UsageFinaliser
from joymesh.runtime_v1.completion.verification import VerificationService
from joymesh.runtime_v1.execution_routing.models import ExecutionResult, ExecutionStatus


class ExecutionCompletionOrchestrator:
    """Converts backend execution facts into runtime lifecycle records.

    .. deprecated::
        Not authoritative for JoyCLI missions. Use
        ``joycli.runtime.completion.ExecutionCompletionOrchestrator``.
    """

    def __init__(
        self,
        *,
        evidence: EvidenceBoundary | None = None,
        verification: VerificationService | None = None,
        usage: UsageFinaliser | None = None,
        graph: MissionGraphProjector | None = None,
        store: CompletionStore | None = None,
    ) -> None:
        warnings.warn(
            "joymesh.runtime_v1.completion.ExecutionCompletionOrchestrator is not "
            "authoritative for JoyCLI missions; use joycli.runtime.completion "
            "(migration stage: joycli-completion-authority-v1).",
            DeprecationWarning,
            stacklevel=2,
        )
        self.evidence = evidence or EvidenceBoundary()
        self.verification = verification or VerificationService()
        self.usage = usage or UsageFinaliser()
        self.graph = graph or MissionGraphProjector()
        self.store = store or CompletionStore()

    async def complete_from_backend(
        self,
        result: ExecutionResult,
        *,
        context: CompletionContext,
        decision: Mapping[str, Any] | None = None,
        candidate_evidence: Sequence[CandidateEvidence] | Sequence[Mapping[str, Any]] = (),
    ) -> CompletionOutcome:
        """Authoritative path after a backend returns ExecutionResult."""

        existing = self.store.get(context.execution_id)
        if existing and existing.state in TERMINAL_COMPLETION_STATES and existing.terminal_emitted:
            return self._outcome_from_record(existing)

        if context.attempt_id != context.authoritative_attempt_id:
            return await self._reject_stale(
                context,
                reason=CompletionFailureClass.STALE_ATTEMPT,
                detail="stale attempt cannot finalise",
            )

        restore_ok = _restore_ok(result)
        if not restore_ok:
            record = self._ensure_record(context, backend_ok=False, restore_ok=False)
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = CompletionFailureClass.RESTORE_FAILED.value
            record.detail = "provider route restoration unresolved"
            self._emit(record, "execution.blocked", {"reason": "restore_failed"})
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))

        record = self._ensure_record(context, backend_ok=result.ok, restore_ok=True)
        record.state = CompletionLifecycleState.BACKEND_COMPLETED
        self._emit(record, "backend.completed", {"ok": result.ok, "message": result.message})
        self.graph.project_attempt(
            execution_id=context.execution_id,
            attempt_id=context.attempt_id,
            payload={"backend_id": context.backend_id, "ok": result.ok},
        )
        if decision:
            self.graph.project_decision(execution_id=context.execution_id, payload=dict(decision))
        self.graph.project_request(
            execution_id=context.execution_id,
            mission_id=context.mission_id,
            payload={"execution_id": context.execution_id},
        )

        if context.cancelled:
            return await self.finalise_cancelled(context, cleanup_completed=True)

        if not result.ok:
            record.state = CompletionLifecycleState.FAILED
            record.failure_class = result.failure_class or "process_failure"
            record.detail = result.message
            self._emit(record, "execution.failed", {"backend_ok": False})
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))

        # Evidence intake
        record.state = CompletionLifecycleState.EVIDENCE_PENDING
        self._emit(record, "evidence.received", {})
        if candidate_evidence:
            accepted, rejected = self.evidence.intake(
                candidate_evidence,
                context=context,
                lifecycle_state=record.state,
                source_backend=context.backend_id,
                source_harness=context.harness_id,
            )
        else:
            accepted, rejected = self.evidence.intake(
                _candidates_from_result(result),
                context=context,
                lifecycle_state=record.state,
                source_backend=context.backend_id,
                source_harness=context.harness_id,
            )
        for item in accepted:
            record.evidence_ids.append(item.evidence_id)
            self.graph.project_evidence(
                execution_id=context.execution_id, evidence=item, accepted=True
            )
            self._emit(record, "evidence.accepted", {"evidence_id": item.evidence_id})
        for item in rejected:
            self.graph.project_evidence(
                execution_id=context.execution_id, evidence=item, accepted=False
            )
            self._emit(
                record,
                "evidence.rejected",
                {
                    "evidence_id": item.evidence_id,
                    "reason": item.rejection_reason.value if item.rejection_reason else None,
                },
            )

        if context.require_evidence and not accepted:
            record.state = CompletionLifecycleState.FAILED
            record.failure_class = CompletionFailureClass.EVIDENCE_MISSING.value
            record.detail = "required evidence missing or rejected"
            self._emit(record, "execution.failed", {"reason": "evidence_missing"})
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))

        record.state = CompletionLifecycleState.EVIDENCE_ACCEPTED
        return await self._run_verification(record, context, result)

    async def complete_from_remote_event(
        self,
        *,
        context: CompletionContext,
        event_type: str,
        payload: Mapping[str, Any],
        candidate_evidence: Sequence[CandidateEvidence] | Sequence[Mapping[str, Any]] = (),
    ) -> CompletionOutcome:
        """Map remote node events into the same completion model."""

        existing = self.store.get(context.execution_id)
        if existing and existing.state in TERMINAL_COMPLETION_STATES and existing.terminal_emitted:
            if event_type in {"task.succeeded", "task.failed", "execution.completed"}:
                self._emit(
                    existing,
                    "execution.late_event_ignored",
                    {"event_type": event_type},
                    force=True,
                )
                return self._outcome_from_record(existing)
            return self._outcome_from_record(existing)

        if context.attempt_id != context.authoritative_attempt_id:
            return await self._reject_stale(
                context,
                reason=CompletionFailureClass.SUPERSEDED_ATTEMPT,
                detail="superseded remote attempt rejected",
            )

        if event_type in {"task.cancelled", "execution.cancelled"}:
            return await self.finalise_cancelled(context, cleanup_completed=True)

        if event_type in {"task.failed", "execution.failed"}:
            record = self._ensure_record(context, backend_ok=False, restore_ok=True)
            record.state = CompletionLifecycleState.FAILED
            record.detail = str(payload.get("detail") or "remote execution failed")
            record.failure_class = str(payload.get("failure_class") or "process_failure")
            self._emit(record, "backend.completed", {"ok": False, "remote": True})
            return self._finalise_terminal(
                record, usage_facts=_usage_from_payload(payload, context)
            )

        if event_type not in {"task.succeeded", "execution.completed", "verification.completed"}:
            # Non-terminal progress events — record only.
            record = self._ensure_record(context, backend_ok=False, restore_ok=True)
            record.state = CompletionLifecycleState.RUNNING
            self._emit(record, event_type, dict(payload))
            self.store.save(record)
            return CompletionOutcome(
                ok=False,
                state=record.state,
                execution_id=context.execution_id,
                mission_id=context.mission_id,
                attempt_id=context.attempt_id,
                detail="progress event recorded",
                events=tuple(record.events),
                record=record.as_dict(),
            )

        # Remote success observation — still requires verification.
        synthetic = ExecutionResult(
            ok=True,
            execution_id=context.execution_id,
            backend_id=context.backend_id,
            harness_id=context.harness_id or "unknown",
            status=ExecutionStatus.SUCCEEDED,
            message="remote backend reported success",
            output=dict(payload),
        )
        # Prefer remote verifier strategy when event carries verification.
        if event_type == "verification.completed" or payload.get("verification"):
            context = CompletionContext(
                organisation_id=context.organisation_id,
                project_id=context.project_id,
                mission_id=context.mission_id,
                execution_id=context.execution_id,
                attempt_id=context.attempt_id,
                authoritative_attempt_id=context.authoritative_attempt_id,
                backend_id=context.backend_id,
                harness_id=context.harness_id,
                correlation_id=context.correlation_id,
                user_id=context.user_id,
                cancelled=context.cancelled,
                require_evidence=True,
                verification_strategy="remote_verifier_event",
                required_evidence_types=context.required_evidence_types or ("remote_verification",),
                max_evidence_bytes=context.max_evidence_bytes,
                metadata=dict(context.metadata),
            )
            if not candidate_evidence and payload.get("verification"):
                candidate_evidence = [
                    CandidateEvidence(
                        evidence_type="remote_verification",
                        sequence=int(payload.get("sequence") or 1),
                        provenance={
                            "execution_id": context.execution_id,
                            "attempt_id": context.attempt_id,
                            "mission_id": context.mission_id,
                            "organisation_id": context.organisation_id,
                            "project_id": context.project_id,
                        },
                        payload=dict(payload.get("verification") or payload),
                    )
                ]
        elif context.require_evidence or context.verification_strategy == "remote_verifier_event":
            pass
        else:
            # Default remote: require explicit verification evidence for success.
            context = CompletionContext(
                organisation_id=context.organisation_id,
                project_id=context.project_id,
                mission_id=context.mission_id,
                execution_id=context.execution_id,
                attempt_id=context.attempt_id,
                authoritative_attempt_id=context.authoritative_attempt_id,
                backend_id=context.backend_id,
                harness_id=context.harness_id,
                correlation_id=context.correlation_id,
                user_id=context.user_id,
                cancelled=context.cancelled,
                require_evidence=True,
                verification_strategy="remote_verifier_event",
                required_evidence_types=("remote_verification",),
                max_evidence_bytes=context.max_evidence_bytes,
                metadata=dict(context.metadata),
            )
        return await self.complete_from_backend(
            synthetic,
            context=context,
            candidate_evidence=candidate_evidence,
        )

    async def finalise_cancelled(
        self,
        context: CompletionContext,
        *,
        cleanup_completed: bool,
    ) -> CompletionOutcome:
        record = self._ensure_record(context, backend_ok=False, restore_ok=True)
        if record.state in TERMINAL_COMPLETION_STATES and record.terminal_emitted:
            return self._outcome_from_record(record)
        if not cleanup_completed:
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = CompletionFailureClass.CLEANUP_FAILED.value
            record.detail = "cancellation cleanup incomplete"
            self._emit(record, "execution.blocked", {"reason": "cleanup_timeout"})
            return self._finalise_terminal(record)
        record.state = CompletionLifecycleState.CANCELLED
        record.detail = "execution cancelled"
        self._emit(record, "execution.cancelled", {"cleanup_completed": True})
        return self._finalise_terminal(record)

    async def resume(self, execution_id: str) -> CompletionOutcome | None:
        """Restart recovery for stuck completion states."""

        record = self.store.get(execution_id)
        if record is None:
            return None
        if record.state in TERMINAL_COMPLETION_STATES:
            return self._outcome_from_record(record)
        context = CompletionContext(
            organisation_id=record.organisation_id,
            project_id=record.project_id,
            mission_id=record.mission_id,
            execution_id=record.execution_id,
            attempt_id=record.attempt_id,
            authoritative_attempt_id=record.authoritative_attempt_id,
            backend_id=record.backend_id,
            harness_id=record.harness_id,
        )
        if record.state in {
            CompletionLifecycleState.BACKEND_COMPLETED,
            CompletionLifecycleState.EVIDENCE_PENDING,
            CompletionLifecycleState.EVIDENCE_ACCEPTED,
            CompletionLifecycleState.VERIFICATION_PENDING,
            CompletionLifecycleState.VERIFYING,
        }:
            # Re-run verification from accepted evidence without re-executing backend.
            synthetic = ExecutionResult(
                ok=record.backend_ok,
                execution_id=record.execution_id,
                backend_id=record.backend_id,
                harness_id=record.harness_id or "unknown",
                status=ExecutionStatus.SUCCEEDED if record.backend_ok else ExecutionStatus.FAILED,
                message="resumed completion",
            )
            return await self._run_verification(record, context, synthetic)
        if record.state is CompletionLifecycleState.FINALISING:
            return self._finalise_terminal(record)
        return self._outcome_from_record(record)

    def list_resumable(self) -> tuple[CompletionRecord, ...]:
        return self.store.list_resumable()

    async def _run_verification(
        self,
        record: CompletionRecord,
        context: CompletionContext,
        result: ExecutionResult,
    ) -> CompletionOutcome:
        record.state = CompletionLifecycleState.VERIFICATION_PENDING
        self._emit(record, "verification.requested", {"strategy": context.verification_strategy})
        record.state = CompletionLifecycleState.VERIFYING
        self._emit(record, "verification.started", {})
        accepted = self.evidence.list_accepted(context.execution_id)
        verify = self.verification.verify(
            VerificationRequest(
                mission_id=context.mission_id,
                execution_id=context.execution_id,
                attempt_id=context.attempt_id,
                strategy=context.verification_strategy,
                required_evidence_types=context.required_evidence_types,
                policy=dict(context.metadata.get("verification_policy") or {}),
                accepted_evidence=accepted,
                backend_ok=result.ok and record.backend_ok,
                restore_ok=record.restore_ok,
            )
        )
        record.verification = verify.as_dict()
        self.graph.project_verification(execution_id=context.execution_id, payload=verify.as_dict())

        if verify.outcome is VerificationOutcome.VERIFIED:
            self._emit(record, "verification.passed", verify.as_dict())
            record.state = CompletionLifecycleState.VERIFIED
            record.detail = verify.detail
            return self._finalise_terminal(
                record,
                success=True,
                usage_facts=_usage_from_result(result, context),
            )
        if verify.outcome is VerificationOutcome.PENDING_HUMAN_APPROVAL:
            self._emit(record, "verification.blocked", verify.as_dict())
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = (
                verify.failure_class.value if verify.failure_class else "verification_inconclusive"
            )
            record.detail = verify.detail
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))
        if verify.outcome is VerificationOutcome.BLOCKED:
            self._emit(record, "verification.blocked", verify.as_dict())
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = (
                verify.failure_class.value if verify.failure_class else "restore_failed"
            )
            record.detail = verify.detail
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))
        if verify.outcome is VerificationOutcome.INCONCLUSIVE:
            self._emit(record, "verification.inconclusive", verify.as_dict())
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = CompletionFailureClass.VERIFICATION_INCONCLUSIVE.value
            record.detail = verify.detail
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))
        if verify.outcome is VerificationOutcome.VERIFICATION_ERROR:
            self._emit(record, "verification.failed", verify.as_dict())
            record.state = (
                CompletionLifecycleState.TIMED_OUT
                if verify.failure_class is CompletionFailureClass.VERIFICATION_TIMEOUT
                else CompletionLifecycleState.FAILED
            )
            record.failure_class = (
                verify.failure_class.value
                if verify.failure_class
                else CompletionFailureClass.VERIFICATION_ERROR.value
            )
            record.detail = verify.detail
            return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))

        self._emit(record, "verification.failed", verify.as_dict())
        record.state = CompletionLifecycleState.FAILED
        record.failure_class = (
            verify.failure_class.value
            if verify.failure_class
            else CompletionFailureClass.VERIFICATION_FAILED.value
        )
        record.detail = verify.detail
        return self._finalise_terminal(record, usage_facts=_usage_from_result(result, context))

    def _finalise_terminal(
        self,
        record: CompletionRecord,
        *,
        success: bool = False,
        usage_facts: Sequence[UsageFact] = (),
    ) -> CompletionOutcome:
        if record.terminal_emitted and record.state in TERMINAL_COMPLETION_STATES:
            return self._outcome_from_record(record)

        record.state = (
            CompletionLifecycleState.FINALISING
            if success
            else record.state
            if record.state in TERMINAL_COMPLETION_STATES
            else CompletionLifecycleState.FAILED
        )
        if success:
            self._emit(record, "execution.finalising", {})
        try:
            usage = self.usage.finalise(
                execution_id=record.execution_id,
                organisation_id=record.organisation_id,
                project_id=record.project_id,
                mission_id=record.mission_id,
                attempt_facts=usage_facts,
            )
            record.usage_finalised = True
            record.usage_aggregate = dict(usage)
            self._emit(record, "usage.finalised", {"execution_id": record.execution_id})
        except Exception as exc:
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = CompletionFailureClass.USAGE_FINALISATION_FAILED.value
            record.detail = f"usage finalisation failed: {type(exc).__name__}"
            self._emit(record, "execution.blocked", {"reason": "usage_finalisation_failed"})
            success = False

        if success:
            record.state = CompletionLifecycleState.COMPLETED
            record.detail = record.detail or "verified completion"
            self._emit(record, "execution.completed", {"verified": True})
        elif record.state is CompletionLifecycleState.FINALISING:
            record.state = CompletionLifecycleState.FAILED

        try:
            node = self.graph.project_result(record)
            record.graph_nodes.append(dict(node))
        except Exception as exc:
            record.state = CompletionLifecycleState.BLOCKED
            record.failure_class = CompletionFailureClass.MISSION_PROJECTION_FAILED.value
            record.detail = f"mission projection failed: {type(exc).__name__}"
            self._emit(record, "execution.blocked", {"reason": "mission_projection_failed"})

        record.terminal_emitted = True
        record.updated_at = utc_now()
        self.store.save(record)
        return self._outcome_from_record(record)

    async def _reject_stale(
        self,
        context: CompletionContext,
        *,
        reason: CompletionFailureClass,
        detail: str,
    ) -> CompletionOutcome:
        record = self._ensure_record(
            context, backend_ok=False, restore_ok=True, update_attempt=False
        )
        self._emit(
            record,
            "execution.stale_attempt_rejected",
            {"attempt_id": context.attempt_id, "reason": reason.value},
            force=True,
        )
        # Do not change terminal state of authoritative execution.
        authoritative = self.store.get(context.execution_id)
        if authoritative and authoritative.state in TERMINAL_COMPLETION_STATES:
            return self._outcome_from_record(authoritative)
        return CompletionOutcome(
            ok=False,
            state=authoritative.state if authoritative else CompletionLifecycleState.RUNNING,
            execution_id=context.execution_id,
            mission_id=context.mission_id,
            attempt_id=context.attempt_id,
            detail=detail,
            failure_class=reason.value,
            events=tuple(record.events),
            record=record.as_dict(),
        )

    def _ensure_record(
        self,
        context: CompletionContext,
        *,
        backend_ok: bool,
        restore_ok: bool,
        update_attempt: bool = True,
    ) -> CompletionRecord:
        existing = self.store.get(context.execution_id)
        if existing is not None:
            if update_attempt:
                existing.backend_ok = backend_ok
                existing.restore_ok = restore_ok
                existing.attempt_id = context.attempt_id
            existing.updated_at = utc_now()
            return existing
        record = CompletionRecord(
            execution_id=context.execution_id,
            mission_id=context.mission_id,
            state=CompletionLifecycleState.RUNNING,
            attempt_id=context.attempt_id,
            authoritative_attempt_id=context.authoritative_attempt_id,
            backend_id=context.backend_id,
            harness_id=context.harness_id,
            organisation_id=context.organisation_id,
            project_id=context.project_id,
            backend_ok=backend_ok,
            restore_ok=restore_ok,
        )
        self.store.save(record)
        return record

    def _emit(
        self,
        record: CompletionRecord,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        force: bool = False,
    ) -> None:
        record.sequence += 1
        event = {
            "event_id": f"evt_{uuid4().hex}",
            "event_type": event_type,
            "organisation_id": record.organisation_id,
            "project_id": record.project_id,
            "mission_id": record.mission_id,
            "execution_id": record.execution_id,
            "attempt_id": record.attempt_id,
            "sequence": record.sequence,
            "timestamp": utc_now().isoformat(),
            "correlation_id": record.execution_id,
            "producer": "ExecutionCompletionOrchestrator",
            "payload": dict(payload),
        }
        # Deduplicate identical terminal events.
        if not force:
            for prior in record.events:
                if (
                    prior.get("event_type") == event_type
                    and prior.get("payload") == event["payload"]
                    and event_type.startswith("execution.")
                    and event_type
                    in {
                        "execution.completed",
                        "execution.failed",
                        "execution.blocked",
                        "execution.cancelled",
                        "usage.finalised",
                    }
                ):
                    return
        record.events.append(event)
        record.updated_at = utc_now()
        self.store.save(record)

    def _outcome_from_record(self, record: CompletionRecord) -> CompletionOutcome:
        return CompletionOutcome(
            ok=record.state is CompletionLifecycleState.COMPLETED,
            state=record.state,
            execution_id=record.execution_id,
            mission_id=record.mission_id,
            attempt_id=record.attempt_id,
            detail=record.detail or record.state.value,
            failure_class=record.failure_class,
            verification=dict(record.verification),
            evidence_ids=tuple(record.evidence_ids),
            usage=dict(record.usage_aggregate),
            events=tuple(record.events),
            graph_nodes=tuple(record.graph_nodes),
            record=record.as_dict(),
        )


def _restore_ok(result: ExecutionResult) -> bool:
    if result.failure_class == "provider_restore_failure":
        return False
    if result.restore_status in {"failed", "unresolved", "blocked"}:
        return False
    output = result.output or {}
    if "restored" in output and output.get("restored") is False:
        return False
    return True


def _candidates_from_result(
    result: ExecutionResult,
) -> list[CandidateEvidence]:
    out: list[CandidateEvidence] = []
    for index, ref in enumerate(result.evidence_refs):
        out.append(
            CandidateEvidence(
                evidence_type="structured_result",
                content_ref=str(ref),
                sequence=index + 1,
                provenance={"execution_id": result.execution_id},
                payload={"ref": str(ref)},
            )
        )
    # Always include a backend output digest candidate when ok.
    if result.ok:
        out.append(
            CandidateEvidence(
                evidence_type="backend_output",
                sequence=len(out) + 1,
                provenance={"execution_id": result.execution_id},
                payload={
                    "message": result.message,
                    "backend_id": result.backend_id,
                    "harness_id": result.harness_id,
                },
                size_bytes=len(result.message),
            )
        )
    verification = result.candidate_verification or result.verification or {}
    if verification:
        out.append(
            CandidateEvidence(
                evidence_type="remote_verification",
                sequence=len(out) + 1,
                provenance={"execution_id": result.execution_id},
                payload=dict(verification),
            )
        )
    return out


def _usage_from_result(
    result: ExecutionResult, context: CompletionContext
) -> tuple[UsageFact, ...]:
    facts = dict(result.usage or {})
    if not facts:
        facts = {"wall_clock_seconds": 0, "input_tokens": 0, "output_tokens": 0}
    return (
        UsageFact(
            organisation_id=context.organisation_id,
            project_id=context.project_id,
            mission_id=context.mission_id,
            execution_id=context.execution_id,
            attempt_id=context.attempt_id,
            backend_id=context.backend_id,
            harness_id=context.harness_id,
            facts=facts,
        ),
    )


def _usage_from_payload(
    payload: Mapping[str, Any], context: CompletionContext
) -> tuple[UsageFact, ...]:
    return (
        UsageFact(
            organisation_id=context.organisation_id,
            project_id=context.project_id,
            mission_id=context.mission_id,
            execution_id=context.execution_id,
            attempt_id=context.attempt_id,
            backend_id=context.backend_id,
            harness_id=context.harness_id,
            facts=dict(payload.get("usage") or {}),
        ),
    )
