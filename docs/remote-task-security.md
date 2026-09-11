# Remote task security

`RemoteTaskEnvelope` is versioned and signed with Ed25519. It contains task and
context ids, organisation, workspace, node, user, browser session, harness,
required capabilities, optional approval, nonce, issued/expiry timestamps, key
id, and signature.

The control plane signs canonical JSON. The node verifies the signature before
reading task content, then validates expiry, replay nonce, node audience,
organisation, workspace grant, route, capabilities, and approval.

Risk levels are LOW, MEDIUM, HIGH, and CRITICAL. Read-only bounded work may be
LOW. File writes and ordinary harness execution are MEDIUM. Installation,
credential, paid-route, and broad shell changes are HIGH and require exact
approval plus recent step-up. CRITICAL actions are unsupported by default.

Output is redacted at the node before transmission and again at cloud ingest.
