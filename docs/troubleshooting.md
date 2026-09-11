# Troubleshooting

## Node remains offline

Confirm system time, DNS, TLS trust, outbound WebSocket access, node revocation
status, and gateway credential expiry. Do not open an inbound firewall port.

## Pairing expires

Start a new session. Codes are short-lived and single use. Check that browser
and node selected the same organisation/workspace.

## Harness is detected but not ready

Inspect authentication, funding confidence, binary fingerprint, certification,
capabilities, and concurrency/quota state. Detection alone is never eligible.

## Approval rejected

Regenerate the plan. Approval fails when user, browser session, node, harness,
executable, argv, directory, environment, risk, hash, or expiry differs.

## Browser progress is not saved

Verify sign-in, same-origin access, the Sites `DB` binding, and D1 migrations.
Authoritative onboarding data must not move to local storage.

## Migration drift

Run `alembic check` at head and `npm run db:generate` after frontend schema
changes.

## Backend exited but mission did not complete

Backend process success is **not** mission completion. Inspect completion events:

* `backend.completed` without `verification.passed`
* `evidence.rejected` / missing required evidence
* `verification.failed` / `verification.inconclusive`
* `execution.blocked` with `restore_failed`
* `execution.stale_attempt_rejected`

See [Execution completion](runtime/execution-completion.md).
