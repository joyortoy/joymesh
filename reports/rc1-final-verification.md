# RC1 Final Verification

## Verdict

```text
RC1 verified and tagged locally
```

## Exact source state

### JoyCLI

* Repository: `intexta-buildweek` (package under `joycli/`)
* Branch: `agent/authenticated-client-server-boundary`
* Commit SHA (wheel + suite): `5fd55fafaeebd2408b69c8417bcf11321351d587`
* Cleanliness: clean detached worktree at that SHA (no untracked source)
* Commits included:

```text
5fd55fa — Remove build-tree egg-info paths from JoyCLI release integrity manifest.
13e6d51 — Mark release checklist commit gate complete for RC1 packaging.
3b0a391 — Declare JoyCLI cryptography dependency and add RC packaging docs.
6fb3899 — Add JoyCLI canonical runtime-state intake and projection routing.
```

Release-blocking defect fixed during this pass: committed integrity manifest listed local `src/joycli.egg-info/*` paths absent from clean checkouts, failing `test_integrity_manifest_matches_committed_file_and_release_metadata`.

### JoyMesh

* Repository: JoyMesh
* Branch: `integration/joyorgenie-os`
* Commit SHA (wheel + suite): `c710b7c3492b011fe6b509bce3523768c630a435`
* Cleanliness: clean detached worktree at that SHA (local `.venv` symlink used only for alembic migration tests; removed for cleanliness checks)
* Commits included:

```text
c710b7c — Commit missing quota package required by signed delivery RC tip.
79ae462 — Sync release checklist commit-gate status into JoyMesh reports.
da67b34 — Add JoyMesh release validation scripts and RC packaging reports.
35bd7f8 — Add JoyMesh signed Unix-socket runtime delivery publisher path.
```

Release-blocking defect fixed during this pass: delivery RC tip imported `joymesh.quota` / `Router(..., quota=...)` but left `src/joymesh/quota/`, routing quota wiring, and quota test harnesses untracked—clean checkouts could not import JoyMesh. Gemini/frontend/website work was excluded.

## Test results

### JoyCLI (clean worktree)

```bash
python -m compileall -q src tests
pytest -q
git diff --check
```

```text
451 passed, 16 warnings in 34.68s
```

### JoyMesh (clean worktree)

```bash
python -m compileall -q src tests
pytest -q
git diff --check
```

```text
467 passed, 6 skipped, 119 warnings in 84.62s
```

(Expected prior baseline ~479/5 included uncommitted Gemini suite work; observed RC tip counts are authoritative.)

## Integration results

All against rebuilt wheels from the clean SHAs above, installed into isolated site-packages (no editable installs; no checkout on `PYTHONPATH` for production proofs).

### Clean wheel install

```bash
# Evidence used prebuilt RC1 wheels (same bytes/hashes as artifacts table)
```

```text
STAGE 9: clean wheel install validation complete
{"directive": "opencode", "ok": true, "selected": "opencode"}
```

Imports resolved under `.../site-packages/`; cryptography 48.0.1 installed via declared deps.

### Signed cross-repository intake

```bash
.venv/bin/python scripts/verify_cross_repo_runtime_intake.py
# RC1: packaged venv; src-path injection disabled
```

```json
{"ok": true, "selected": "opencode", "root": "..."}
```

### Runtime routing E2E

```text
runtime routing e2e: ok
```

### Live OpenCode crash recovery

```bash
JOYMESH_LIVE_OPENCODE_CRASH=1 \
.venv/bin/python scripts/verify_opencode_crash_recovery.py
# RC1 packaged variant: no checkout PYTHONPATH injection
```

```text
mode=clean_retry; final_status=cancelled; orphans=[]; post_recovery_intake_size after>before; snapshot drain acked=1
```

### Fresh install

```bash
bash scripts/verify_fresh_install.sh <joymesh-wheel>
# JOYCLI_WHEEL=<joycli-wheel>
```

```text
STAGE 11: fresh-install ritual complete
```

## Artifacts

| Field | JoyCLI | JoyMesh |
|-------|--------|---------|
| filename | `joycli-0.26.0-py3-none-any.whl` | `joymesh-0.1.0-py3-none-any.whl` |
| size | 242982 | 381934 |
| SHA-256 | `f0ed5f6fe1d5c5409088ac7be6a0a775ba20ae9e1743e6181b8ff1d55f344503` | `78bf7cda024315c12e86acc520fc519ced9e5b4a3236e60be9aa8e8089044bc0` |
| source commit | `5fd55fa…` | `c710b7c…` |
| build | `python -m build --wheel` / Python 3.12.13 | same |
| metadata | `Requires-Dist: cryptography>=42,<51` | runtime deps include cryptography; quota+delivery present |
| content audit | no `__pycache__`/`.pyc`/abs paths/keys | same |

## Security

* JoyCLI `allow_unsigned` defaults to **False** (`JOYCLI_RUNTIME_ALLOW_UNSIGNED` unset → signatures required).
* Unsigned / insecure mode is test-only and warns when enabled.
* Publisher/key/organisation binding exercised by clean-wheel + cross-repo proofs (ACK only after verify + durable commit).
* No private key material enters JoyCLI wheels or intake path.
* `pip-audit` on packaged runtime env: **No known vulnerabilities found**; local `joycli`/`joymesh` skipped (unpublished on PyPI).
* Bootstrap-tool advisories (if any in other venvs) are not treated as JoyMesh runtime CVEs.

## Tags

Created locally after gates passed (not pushed):

* JoyCLI: `joycli-v0.26.0-rc1` → `5fd55fafaeebd2408b69c8417bcf11321351d587`
* JoyMesh: `joymesh-v0.1.0-rc1` → `c710b7c3492b011fe6b509bce3523768c630a435`

Annotated messages include version, commit, wheel filename/SHA-256, test counts, validation summary, and known limitations.

Report-sync commits after tagging (if present) update documentation only; wheels were built from the tagged SHAs above.

## Remaining limitations

1. JoyMesh hatchling builds may not be bit-reproducible across hosts.
2. Key distribution and rotation remain operator-managed; no KMS.
3. Upstream crash-recovery helper may still inject checkout `PYTHONPATH` for child execution; RC1 packaged proof ran without that injection.
4. Deprecated JoyMesh intake remains test-only.
5. Dirty original worktrees still exist but remained isolated from RC.
6. OpenCode availability is environment-dependent for live crash/fresh rituals.

## Push status

```text
Nothing was pushed.
```
EOF

# Mirror into JoyCLI reports
cp /Users/joytan/Documents/joymesh/reports/rc1-final-verification.md /Users/joytan/intexta-buildweek/joycli/reports/rc1-final-verification.md

# Update JoyCLI checklist similarly for tags
python3 - <<'PY'
from pathlib import Path
p = Path('/Users/joytan/intexta-buildweek/joycli/reports/release-checklist.md')
text = p.read_text()
text = text.replace('- [ ] Official RC tag (not created this phase)', '- [x] Local RC1 annotated tag created (`joycli-v0.26.0-rc1`)')
p.write_text(text)
print('joycli checklist updated')
PY
