# Release Readiness Report — JoyMux / JoyCLI RC1

**Date:** 2026-08-03  
**Verdict:** **Suitable for Release Candidate (RC1)** with documented limitations.

## Architecture freeze

Ownership boundary unchanged:

```text
JoyMesh observe/execute/publish/sign
  → JoyCLI verify/auth/persist/project/route
  → ExecutionDirective
  → JoyMesh launch revalidation
```

No protocol expansion. No authority redesign.

## Packaging defects fixed this phase

1. JoyCLI production cryptography import was undeclared → now required runtime dependency + Requires-Dist.
2. JoyCLI wheel accidentally packaged `__pycache__` → build backend ignore rules.

## Verification summary

| Gate | Result |
|------|--------|
| JoyCLI `pytest -q` | 451 passed |
| Clean wheel install script | Pass |
| Cross-repo signed intake | Pass |
| Runtime routing E2E | Pass |
| pip-audit (cryptography pin) | No known vulnerabilities |
| JoyMesh pip-audit | Only bootstrap `pip` advisories in local venv after ensurepip; package deps not flagged beyond unauditable local joymesh |

## Remaining known limitations

1. JoyMesh hatchling builds are not claimed bit-reproducible across hosts.
2. Key distribution/rotation is operator-manual (no KMS).
3. Worktrees still contain unrelated dirty files (website/frontend/experiments) that must not ship in RC commits.
4. Live OpenCode crash-recovery and full fresh-install rituals should be re-run on the exact RC artifacts immediately before tagging.
5. JoyMesh local venv historically lacked `pip`; audit tooling depends on environment bootstrap.
6. Crash-recovery helper still may inject checkout `PYTHONPATH` for child processes; production path is wheel-based (`verify_clean_wheel_install.sh` / `verify_fresh_install.sh`).

## Recommendation

Promote **RC1** for controlled integration after:

* logically scoped commits (no website/frontend),
* final artifact SHA recording,
* one more clean-wheel + crash-recovery run against those exact SHAs.
