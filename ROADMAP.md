# JoyMesh roadmap

JoyMesh is building a trustworthy layered delegation system for coding agents.
The roadmap favors measurable context isolation, verifiable execution, and a
small number of complete parallel workflows over broad compatibility claims.

## Now: trustworthy alpha

- Keep tests, lint, type-checking, and package builds green in CI.
- Provide credential-free discovery and parallel-delegation demos.
- Connect `ParallelDelegator` to route preview, execution, and compact result
  normalization through the public SDK.
- Record version-specific certification evidence for Codex and OpenCode.
- Publish a support matrix that separates catalogue, detected, conformant, and
  certified states.
- Prepare the first tagged source and wheel release.

## Next: useful integrations

- Publish focused Codex and OpenCode integration examples.
- Publish a reproducible OpenCode + DeepSeek delegation example.
- Make route decisions easier to inspect and explain.
- Measure planner context, worker tokens, elapsed time, and feedback size so
  users can verify whether delegation actually saves resources.
- Improve platform coverage and installation guidance for Linux and Windows.
- Add generated reference documentation for the Python SDK and REST API.
- Turn recurring setup failures into `joymesh doctor` diagnostics.

## Later: production confidence

- Add supported PostgreSQL persistence and distributed supervision.
- Expand remote-node operational guidance and recovery testing.
- Add compatibility fixtures for more harness versions.
- Define stable protocol and deprecation guarantees before a 1.0 release.

## How to participate

Open an issue before starting a substantial feature. New contributors can begin
with issues labeled [`good first issue`](https://github.com/joyortoy/joymesh/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22),
and connector specialists can look for
[`help wanted`](https://github.com/joyortoy/joymesh/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22).

Roadmap items are priorities, not promises. Their order may change based on
evidence from users and contributors.
