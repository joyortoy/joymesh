# Release Checklist

- [x] JoyCLI declares runtime dependency `cryptography>=42,<51`
- [x] JoyCLI wheel METADATA includes Requires-Dist cryptography
- [x] JoyCLI wheel excludes `__pycache__`
- [x] JoyMesh declares cryptography among runtime deps
- [x] Clean venv installs both wheels without PYTHONPATH
- [x] Signed publish → durable ACK
- [x] Projection survives reopen
- [x] Canonical routing + ExecutionDirective from projection
- [x] Cross-repo signed intake script pass
- [x] Runtime routing E2E pass
- [x] JoyCLI full suite 451 passed
- [x] Release docs present (INSTALL/RELEASE/OPERATIONS/UPGRADE/SECURITY/ARCHITECTURE)
- [x] Reports generated under `reports/`
- [ ] Git commits logically separated (in progress)
- [ ] Push (forbidden this phase)
- [ ] Official RC tag (not created this phase)

## Operational checklist before RC promote

1. Backup JoyCLI state directories
2. Provision production Ed25519 keypair; store private key only on JoyMesh hosts
3. Set `JOYCLI_RUNTIME_ALLOW_UNSIGNED=0`
4. Deploy JoyCLI intake before JoyMesh publishers
5. Confirm readiness `ready=true`
6. Confirm JoyMesh delivery health connected and outbox drained
