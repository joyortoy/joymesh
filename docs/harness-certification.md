# Harness certification

Onboarding certification uses the real discovered binary in an isolated
temporary Git repository. Evidence includes adapter and JoyMesh versions,
binary version and fingerprint, OS, test-suite version, checks, diagnostics,
provider configuration fingerprint, and timestamp. Paid inference requires an
explicit cost-aware approval. Binary, adapter, environment, auth, or routing
changes invalidate applicable evidence.

Support has two independent states:

1. Adapter certification proves that the adapter implementation passes the
   reusable JoyMesh conformance suite.
2. Binary certification proves the same contract against one executable path
   and version.

Certification evidence records the adapter version, binary version, executable
path, individual checks, timestamp, and failure detail. A new binary version is
uncertified until it produces its own evidence.

The shared gate covers installation detection, version reporting, manifest and
launch shape, environment filtering, workspace propagation, streaming,
normalization and ordering, cancellation, timeouts, process-tree cleanup,
completion and failure, optional sessions and usage, rate-limit/quota
classification, secret redaction, and unsupported features.

`joymesh harness certify <id>` emits a plan because real certification can
execute tools, modify a disposable workspace, and consume provider quota. Fake
binary certification is exercised in the automated test suite without external
side effects.
