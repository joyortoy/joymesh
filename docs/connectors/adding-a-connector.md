# Adding a connector

## Catalogue (discovery catalogue)

1. Review official product documentation and source releases.
2. Add one versioned catalogue file with an installation fingerprint and review timestamp.
3. Declare the real executable and documented headless contract; use IDE-only or discovery-only
   when no machine interface exists.
4. Add an adapter, parser fixtures, authentication inspection, and provider modes.
5. Run schema, injection, conformance, process cleanup, and redaction tests.
6. Add a real-binary certification profile and record evidence without embedding credentials.

Adding a catalogue record never edits central routing logic and never makes the connector
routable.

## Runtime v1 connector (#6 and beyond)

1. Implement `src/joymesh/runtime_v1/connectors/<id>.py` for `ConnectorRuntime`
2. Declare capabilities; implement discovery, auth, argv, `execution_environment`, events
3. Register only in `builtin_connectors()`
4. Add conformance tests and optional `JOYMESH_LIVE_<ID>=1`
5. Validate with `joymesh connector live-test <id> [--json]`

Do not add connector-ID branches to `node_runner`, certification, readiness, service,
shared live-test, or CLI rendering. See `docs/runtime/connector-protocol.md`.
