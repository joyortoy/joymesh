# Release Readiness Report — JoyMux / JoyCLI RC1

**Date:** 2026-08-03  
**Verdict:** **RC1 verified and tagged locally** (not pushed).

## Architecture freeze

Ownership boundary unchanged:

```text
JoyMesh observe/execute/publish/sign
  → JoyCLI verify/auth/persist/project/route
  → ExecutionDirective
  → JoyMesh launch revalidation
```

## Clean-worktree verification summary

| Gate | Result |
|------|--------|
| JoyCLI clean HEAD | `5fd55fa` (integrity-manifest fix after `13e6d51`) |
| JoyMesh clean HEAD (wheel source) | `c710b7c` (quota incompleteness fix after `79ae462`) |
| JoyCLI `pytest -q` | 451 passed |
| JoyMesh `pytest -q` | 467 passed, 6 skipped, 0 failed |
| Clean wheel install | Pass (exact rebuilt wheels) |
| Cross-repo signed intake | `{"ok": true, "selected": "opencode"}` |
| Runtime routing E2E | `runtime routing e2e: ok` |
| Live OpenCode crash recovery | Pass (`clean_retry`, cancel, no orphans, drain) |
| Fresh install | Pass |
| pip-audit (packaged runtime env) | No known vulnerabilities (local packages unauditable on PyPI) |

## Remaining known limitations

1. JoyMesh hatchling builds are not claimed bit-reproducible across hosts.
2. Key distribution/rotation is operator-manual (no KMS).
3. Upstream crash-recovery helper may still inject checkout `PYTHONPATH`; RC1 packaged validation ran without that injection successfully.
4. Deprecated JoyMesh intake remains test/reference only.
5. Dirty original worktrees still exist and must stay isolated from RC.
6. Live OpenCode availability is environment-dependent.

## Recommendation

Local RC1 tags only. Do not push until explicitly instructed.
