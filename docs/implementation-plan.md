# Unified harness lifecycle implementation plan

JoyMesh remains a backend-only, SDK-first interoperability layer. The Python
service is the sole orchestration implementation; the CLI and REST API are
transport wrappers around it.

1. Define immutable harness, capability, installation, authentication, funding,
   lifecycle-plan, routing-transform, and certification contracts.
2. Populate a declarative catalogue only from official documentation. Catalogue
   entries distinguish a stable adapter contract from the certification state of
   any particular installed binary.
3. Add deterministic, policy-controlled discovery. Locating files is read-only;
   executing a version probe is separately opt-in.
4. Add install, upgrade, uninstall, and login planners. Execution requires an
   explicit approval token, never uses a shell, and is not part of routing.
5. Register built-in adapters through a registry with aliases and Python entry
   point loading. Preserve the existing adapter API as a compatibility facade.
6. Run Codex, OpenCode, Claude Code, and Gemini CLI through the shared process
   runtime. Add only documented Tier 2 non-interactive interfaces and identify
   experimental or unsupported boundaries explicitly.
7. Model FireConnect, Fireworks, and compatible external routers as route
   transforms, not coding harnesses. Transform activation is approval-gated.
8. Persist version-aware certification evidence in a new migration and expose
   lifecycle/certification operations through the SDK, then thin CLI and REST
   wrappers.
9. Generate the published capability matrix from the catalogue and enforce it
   with tests.

No package installation, upgrade, interactive login, paid inference run, or
provider-routing mutation is part of repository validation.
