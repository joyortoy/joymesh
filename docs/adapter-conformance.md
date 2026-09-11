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

Cross-adapter tests additionally prove that Codex, OpenCode, Claude Code,
and Gemini CLI execute the same `RunRequest` through the same `JoyMesh` service
contract (a test-only fake adapter may be injected explicitly; it is not part
of the production registry). Routing,
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

The production registry does not include a fake harness. Tests may inject a
test-only fake adapter explicitly; that path is never the production default.

## Status meanings

- **Fake-native conformance passed** (test-only) means a deterministic executable that
  speaks the documented native shape passed the runtime contract. It validates
  JoyMesh code, not an upstream release.
- **Real-binary certification** is evidence tied to the executable path,
  upstream version, JoyMesh version, operating system, and suite version.
- **Supported** requires applicable adapter conformance and current real-binary
  certification.
- **Experimental** means an official programmatic interface is implemented but
  one or both certification layers are incomplete.
- **Detected but unsupported** means discovery found a product for which
  JoyMesh has no stable, officially documented execution contract. Detection
  never promotes it to runnable support.
