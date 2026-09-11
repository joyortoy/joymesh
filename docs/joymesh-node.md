# JoyMesh Node

JoyMesh Node is a local process, not a browser agent. It owns harness discovery,
plan generation, safe process execution, event normalization, redaction,
workspace enforcement, and local credential interaction.

The node:

- creates an Ed25519 key locally and stores the private key with mode `0600` or
  in the operating system credential store;
- pairs through PKCE or a short-lived device code;
- opens an outbound `wss://` connection and never requires an inbound port;
- sends heartbeats and reconnects with capped exponential backoff and jitter;
- validates signature, key id, audience, expiry, nonce, node id, organisation,
  workspace grant, capability, and approval before execution;
- maintains bounded replay state and structured event sequence numbers;
- cancels the complete process tree and cleans temporary workspaces on shutdown.

`http://localhost` WebSockets are accepted only for development. Other endpoints
must use TLS.
