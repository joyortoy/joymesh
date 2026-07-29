# Connector platform

JoyMesh treats a coding harness, an IDE integration, and an inference provider as different
objects. Harness definitions live in `src/joymesh/connectors/catalogue/*.yaml`; the SDK, CLI, REST
API, routing compatibility layer, and onboarding consume that versioned source.

Catalogue presence is not a support claim. A connector advances through catalogued,
discoverable, installable, authenticatable, adapter-conformant, real-binary-tested, certified,
and production-ready states. Blocked and deprecated are terminal policy states.

Only a certified or production-ready connector may be considered for automatic routing, and
only when its executable, authentication, version-bound certification, certified capabilities,
user opt-in, and node health all remain valid. This repository currently marks no external
connector certified.
