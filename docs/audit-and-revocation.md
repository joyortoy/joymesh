# Audit and revocation

Audit events record organisation, actor, action, target type/id, outcome,
timestamp, and redacted metadata. They cover sign-in, pairing, node key
rotation, grants, lifecycle plans, approvals, authentication status,
certification, routing changes, remote task creation/cancellation, fallback, and
revocation.

Audit records are append-only to application code. Secret values, raw task
output, cookies, bearer credentials, and recovery codes are excluded.

Session revocation blocks browser writes. Node revocation closes presence,
rejects new task envelopes, and marks outstanding offers cancelled. Workspace
grant revocation blocks future filesystem access. Certification revocation
removes the installation from eligible routes without deleting evidence.
