# Harness architecture

`joymesh.harnesses` separates facts about a product from executable code:

- `contracts` contains immutable public SDK models.
- `catalogue` contains official-source-backed definitions.
- `registry` resolves stable IDs and aliases, owns adapter instances, and can
  load optional `joymesh.harness_adapters` entry points.
- `discovery` locates all executable candidates deterministically. It does not
  run a discovered file unless `DiscoveryPolicy.execute_version_commands` is
  explicitly enabled.
- `lifecycle` creates install, upgrade, uninstall, and login plans. A plan is
  inert until an SDK consumer supplies a matching `ApprovalToken`.
- `adapters` translates documented native protocols into `NormalizedEvent`.
- `certification` names the reusable support gate and persists evidence
  separately for the adapter version and installed binary version.

The existing `AdapterRegistry` remains as a compatibility name for
`HarnessRegistry`. Routing sees only registered executable adapters; catalogue
entries such as Amazon Q can remain discoverable without being falsely
advertised as runnable.

The REST API and Typer CLI call `JoyMesh`, the same service used directly by
Python consumers. They contain no separate routing or execution logic.

FireConnect, Fireworks routers, and compatible external routers transform
provider/model/funding selections. They do not appear as coding harnesses and
cannot silently change a billing route.
