"""Completion lifecycle states — distinct from backend process exit."""

from __future__ import annotations

from enum import StrEnum


class CompletionLifecycleState(StrEnum):
    QUEUED = "queued"
    ROUTING = "routing"
    PREPARING = "preparing"
    RUNNING = "running"
    BACKEND_COMPLETED = "backend_completed"
    EVIDENCE_PENDING = "evidence_pending"
    EVIDENCE_ACCEPTED = "evidence_accepted"
    VERIFICATION_PENDING = "verification_pending"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FINALISING = "finalising"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_COMPLETION_STATES = frozenset(
    {
        CompletionLifecycleState.COMPLETED,
        CompletionLifecycleState.FAILED,
        CompletionLifecycleState.BLOCKED,
        CompletionLifecycleState.CANCELLED,
        CompletionLifecycleState.TIMED_OUT,
    }
)

RESUMABLE_COMPLETION_STATES = frozenset(
    {
        CompletionLifecycleState.BACKEND_COMPLETED,
        CompletionLifecycleState.EVIDENCE_PENDING,
        CompletionLifecycleState.EVIDENCE_ACCEPTED,
        CompletionLifecycleState.VERIFICATION_PENDING,
        CompletionLifecycleState.VERIFYING,
        CompletionLifecycleState.FINALISING,
    }
)


class EvidenceTrustClassification(StrEnum):
    UNTRUSTED_BACKEND_OUTPUT = "untrusted_backend_output"
    DECLARED = "declared"
    ACCEPTED = "accepted"
    VERIFIED = "verified"
    REJECTED = "rejected"


class VerificationOutcome(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    VERIFICATION_ERROR = "verification_error"
    PENDING_HUMAN_APPROVAL = "pending_human_approval"


class EvidenceRejectionReason(StrEnum):
    IDENTITY_MISMATCH = "identity_mismatch"
    WRONG_TENANT = "wrong_tenant"
    WRONG_PROJECT = "wrong_project"
    WRONG_MISSION = "wrong_mission"
    WRONG_EXECUTION = "wrong_execution"
    WRONG_ATTEMPT = "wrong_attempt"
    INVALID_SEQUENCE = "invalid_sequence"
    DUPLICATE_CONFLICTING = "duplicate_conflicting"
    UNSUPPORTED_TYPE = "unsupported_evidence_type"
    OVERSIZED = "oversized_evidence"
    INVALID_CONTENT_HASH = "invalid_content_hash"
    MISSING_PROVENANCE = "missing_provenance"
    STALE_ATTEMPT = "stale_attempt"
    LATE_AFTER_TERMINAL = "late_evidence_after_terminal_state"
    CANCELLED_EXECUTION = "cancelled_execution"


class CompletionFailureClass(StrEnum):
    EVIDENCE_REJECTED = "evidence_rejected"
    EVIDENCE_MISSING = "evidence_missing"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_INCONCLUSIVE = "verification_inconclusive"
    VERIFICATION_TIMEOUT = "verification_timeout"
    VERIFICATION_ERROR = "verification_error"
    STALE_ATTEMPT = "stale_attempt"
    SUPERSEDED_ATTEMPT = "superseded_attempt"
    LATE_EVENT = "late_event"
    USAGE_FINALISATION_FAILED = "usage_finalisation_failed"
    MISSION_PROJECTION_FAILED = "mission_projection_failed"
    RESTORE_FAILED = "restore_failed"
    CLEANUP_FAILED = "cleanup_failed"
    LEASE_LOST = "lease_lost"
