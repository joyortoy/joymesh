# Installation Validation

## Clean environment sequence (executed)

1. Rebuild JoyCLI + JoyMesh wheels
2. Create fresh venv (no `PYTHONPATH`)
3. `pip install` both wheels
4. Assert imports resolve from `site-packages`
5. Assert `cryptography` importable
6. Provision Ed25519 keypair (private → JoyMesh env, public → JoyCLI env)
7. Start `joyctl runtime intake-serve`
8. JoyMesh signed snapshot → ACK (outbox 0)
9. Publish eligible signed synthetic snapshot
10. Canonical `route_provider` + `build_execution_directive` select `opencode`
11. Reopen SQLite store — projection retained

Command: `joymesh/scripts/verify_clean_wheel_install.sh`

Result: **PASS** (`{"ok": true, "selected": "opencode", "directive": "opencode"}`)

## Additional packaged/source-assisted validations

| Script | Result |
|--------|--------|
| `verify_cross_repo_runtime_intake.py` | Pass |
| `verify_runtime_routing_e2e.py` | Pass |
| `verify_fresh_install.sh` | Pass (prior packaging track; re-run recommended after RC tag) |
| Live OpenCode crash recovery | Pass on prior run with shared signing key |

## Failures encountered during this phase

* Initial JoyCLI wheel included `__pycache__` (~1.4MB) — **fixed** in build backend ignore rules.
* Routing against raw detect snapshot correctly rejected unauthenticated harnesses — validation adjusted to also publish an eligible signed snapshot for packaging proof (not a product defect).
