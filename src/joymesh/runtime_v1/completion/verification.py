"""Provider-neutral verification service — backends never own mission verification."""

from __future__ import annotations

from typing import Any

from joymesh.runtime_v1.completion.models import VerificationRequest, VerificationResult
from joymesh.runtime_v1.completion.states import CompletionFailureClass, VerificationOutcome


class VerificationService:
    """Deterministic verification strategies over accepted evidence."""

    def verify(self, request: VerificationRequest) -> VerificationResult:
        if request.policy.get("cancelled"):
            return VerificationResult(
                outcome=VerificationOutcome.CANCELLED,
                strategy=request.strategy,
                detail="verification cancelled",
            )
        if not request.restore_ok:
            return VerificationResult(
                outcome=VerificationOutcome.BLOCKED,
                strategy=request.strategy,
                detail="provider route restoration unresolved",
                failure_class=CompletionFailureClass.RESTORE_FAILED,
                checks={"restore_ok": False},
            )
        if request.policy.get("require_human_approval"):
            return VerificationResult(
                outcome=VerificationOutcome.PENDING_HUMAN_APPROVAL,
                strategy=request.strategy,
                detail="human approval required",
                failure_class=CompletionFailureClass.VERIFICATION_INCONCLUSIVE,
            )
        if request.policy.get("force_timeout"):
            return VerificationResult(
                outcome=VerificationOutcome.VERIFICATION_ERROR,
                strategy=request.strategy,
                detail="verification timed out",
                failure_class=CompletionFailureClass.VERIFICATION_TIMEOUT,
            )
        if request.policy.get("force_error"):
            return VerificationResult(
                outcome=VerificationOutcome.VERIFICATION_ERROR,
                strategy=request.strategy,
                detail="verification infrastructure error",
                failure_class=CompletionFailureClass.VERIFICATION_ERROR,
            )

        strategy = request.strategy
        if strategy == "always_pass":
            return VerificationResult(
                outcome=VerificationOutcome.VERIFIED,
                strategy=strategy,
                detail="strategy always_pass",
                checks={"backend_ok": request.backend_ok},
            )
        if strategy == "artifact_existence":
            return self._artifact_existence(request)
        if strategy == "test_command":
            return self._test_command(request)
        if strategy == "content_hash_match":
            return self._content_hash_match(request)
        if strategy == "schema_validation":
            return self._schema_validation(request)
        if strategy == "composite":
            return self._composite(request)
        if strategy == "remote_verifier_event":
            return self._remote_verifier(request)
        # Default: backend success + optional required evidence types.
        return self._backend_success_with_evidence(request)

    def _backend_success_with_evidence(self, request: VerificationRequest) -> VerificationResult:
        if not request.backend_ok:
            return VerificationResult(
                outcome=VerificationOutcome.FAILED,
                strategy=request.strategy,
                detail="backend did not succeed",
                failure_class=CompletionFailureClass.VERIFICATION_FAILED,
                checks={"backend_ok": False},
            )
        missing = [
            item
            for item in request.required_evidence_types
            if not any(ev.evidence_type == item for ev in request.accepted_evidence)
        ]
        if missing:
            return VerificationResult(
                outcome=VerificationOutcome.FAILED,
                strategy=request.strategy,
                detail=f"missing required evidence: {', '.join(missing)}",
                failure_class=CompletionFailureClass.EVIDENCE_MISSING,
                checks={"missing": missing},
            )
        return VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            strategy=request.strategy,
            detail="backend success with required evidence",
            checks={
                "backend_ok": True,
                "evidence_count": len(request.accepted_evidence),
            },
        )

    def _artifact_existence(self, request: VerificationRequest) -> VerificationResult:
        required = tuple(
            request.policy.get("required_artifacts") or request.required_evidence_types
        )
        present = {
            str(ev.payload.get("artifact") or ev.content_ref or ev.evidence_type)
            for ev in request.accepted_evidence
        }
        missing = [item for item in required if item not in present]
        if missing:
            return VerificationResult(
                outcome=VerificationOutcome.FAILED,
                strategy="artifact_existence",
                detail=f"missing artifacts: {', '.join(missing)}",
                failure_class=CompletionFailureClass.EVIDENCE_MISSING,
                checks={"missing": missing},
            )
        return VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            strategy="artifact_existence",
            detail="required artifacts present",
            checks={"artifacts": sorted(present)},
        )

    def _test_command(self, request: VerificationRequest) -> VerificationResult:
        for ev in request.accepted_evidence:
            if ev.evidence_type in {"test_summary", "test_command"}:
                passed = bool(ev.payload.get("passed", ev.payload.get("ok")))
                if passed:
                    return VerificationResult(
                        outcome=VerificationOutcome.VERIFIED,
                        strategy="test_command",
                        detail="tests passed",
                        checks=dict(ev.payload),
                    )
                return VerificationResult(
                    outcome=VerificationOutcome.FAILED,
                    strategy="test_command",
                    detail="tests failed",
                    failure_class=CompletionFailureClass.VERIFICATION_FAILED,
                    checks=dict(ev.payload),
                )
        return VerificationResult(
            outcome=VerificationOutcome.FAILED,
            strategy="test_command",
            detail="no test evidence",
            failure_class=CompletionFailureClass.EVIDENCE_MISSING,
        )

    def _content_hash_match(self, request: VerificationRequest) -> VerificationResult:
        expected = str(request.policy.get("expected_hash") or "")
        if not expected:
            return VerificationResult(
                outcome=VerificationOutcome.INCONCLUSIVE,
                strategy="content_hash_match",
                detail="expected_hash not configured",
                failure_class=CompletionFailureClass.VERIFICATION_INCONCLUSIVE,
            )
        for ev in request.accepted_evidence:
            if ev.content_hash == expected:
                return VerificationResult(
                    outcome=VerificationOutcome.VERIFIED,
                    strategy="content_hash_match",
                    detail="content hash matched",
                    checks={"hash": expected},
                )
        return VerificationResult(
            outcome=VerificationOutcome.FAILED,
            strategy="content_hash_match",
            detail="content hash mismatch",
            failure_class=CompletionFailureClass.VERIFICATION_FAILED,
        )

    def _schema_validation(self, request: VerificationRequest) -> VerificationResult:
        required_keys = tuple(request.policy.get("required_keys") or ())
        for ev in request.accepted_evidence:
            if ev.evidence_type == "structured_result":
                missing = [key for key in required_keys if key not in ev.payload]
                if missing:
                    return VerificationResult(
                        outcome=VerificationOutcome.FAILED,
                        strategy="schema_validation",
                        detail=f"schema missing keys: {', '.join(missing)}",
                        failure_class=CompletionFailureClass.VERIFICATION_FAILED,
                    )
                return VerificationResult(
                    outcome=VerificationOutcome.VERIFIED,
                    strategy="schema_validation",
                    detail="schema valid",
                )
        return VerificationResult(
            outcome=VerificationOutcome.FAILED,
            strategy="schema_validation",
            detail="no structured_result evidence",
            failure_class=CompletionFailureClass.EVIDENCE_MISSING,
        )

    def _remote_verifier(self, request: VerificationRequest) -> VerificationResult:
        for ev in request.accepted_evidence:
            if ev.evidence_type in {"remote_verification", "verification"}:
                outcome = str(ev.payload.get("outcome") or "")
                if outcome == "verified" or ev.payload.get("passed") is True:
                    return VerificationResult(
                        outcome=VerificationOutcome.VERIFIED,
                        strategy="remote_verifier_event",
                        detail="remote verifier accepted",
                        checks=dict(ev.payload),
                    )
                if outcome == "failed" or ev.payload.get("passed") is False:
                    return VerificationResult(
                        outcome=VerificationOutcome.FAILED,
                        strategy="remote_verifier_event",
                        detail="remote verifier rejected",
                        failure_class=CompletionFailureClass.VERIFICATION_FAILED,
                        checks=dict(ev.payload),
                    )
        if request.backend_ok and not request.required_evidence_types:
            # Remote completion without verification evidence cannot pass when strategy requires it.
            return VerificationResult(
                outcome=VerificationOutcome.FAILED,
                strategy="remote_verifier_event",
                detail="remote completion without verification evidence",
                failure_class=CompletionFailureClass.EVIDENCE_MISSING,
            )
        return self._backend_success_with_evidence(request)

    def _composite(self, request: VerificationRequest) -> VerificationResult:
        strategies = tuple(request.policy.get("strategies") or ("backend_success_with_evidence",))
        checks: dict[str, Any] = {}
        for strategy in strategies:
            child = VerificationRequest(
                mission_id=request.mission_id,
                execution_id=request.execution_id,
                attempt_id=request.attempt_id,
                strategy=str(strategy),
                required_evidence_types=request.required_evidence_types,
                policy=dict(request.policy.get("child_policy") or {}),
                accepted_evidence=request.accepted_evidence,
                backend_ok=request.backend_ok,
                restore_ok=request.restore_ok,
            )
            result = self.verify(child)
            checks[str(strategy)] = result.as_dict()
            if result.outcome is not VerificationOutcome.VERIFIED:
                return VerificationResult(
                    outcome=result.outcome,
                    strategy="composite",
                    detail=f"composite failed at {strategy}: {result.detail}",
                    failure_class=result.failure_class,
                    checks=checks,
                )
        return VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            strategy="composite",
            detail="all composite strategies verified",
            checks=checks,
        )
