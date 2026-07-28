# Adapter conformance

An adapter is not supported merely because it can be registered or detected.
JoyMesh marks production adapter instances `experimental` until that adapter has
passed the shared conformance suite for its release.

The reusable suite lives in `tests/conformance.py`. Every adapter is
parameterized through `tests/test_adapter_conformance.py`; adapter-specific
copies of these tests are not accepted.

## Required checks

The suite covers:

- installation detection and version reporting
- capability manifest validity and unsupported-feature errors
- launch specification generation, workspace propagation, and environment
  filtering
- streaming normalization, run/event identity, and sequence ordering
- completion, native failure propagation, timeout, cancellation, and process
  tree cleanup
- native session extraction and resume for adapters that advertise resume
- observed usage extraction
- quota and rate-limit classification
- secret redaction

Cross-adapter tests additionally prove that fake, Codex, and OpenCode execute
the same `RunRequest` through the same `JoyMesh` service contract. Routing,
fallback approval, linked continuations, concurrent isolation, and the
SDK-first acceptance flow live in `tests/test_mesh_integration.py`.

## Support gate

Built-in adapter classes default to `experimental`. A release may set its
support metadata to `supported` only after:

1. the adapter passes every applicable shared conformance assertion;
2. its native CLI command contract is checked against current upstream
   documentation;
3. cross-adapter integration and concurrency tests pass;
4. known unsupported capabilities are reported explicitly.

The fake adapter is always supported because it is bundled and is itself run
through the same suite.
