# Threat model

## Assets and boundaries

Assets are local source, harness credentials, subscription value, node private
keys, browser sessions, approval authority, task integrity, event
confidentiality, and audit integrity. The browser is untrusted for
authorization. The cloud cannot access local files without a signed task and
node grant. Harness output is untrusted input. TLS protects transport;
application signatures protect task integrity and node authentication.

## Principal threats and controls

- Session theft: Secure HttpOnly cookies, hash at rest, rotation, revocation,
  CSRF/origin enforcement, and passkey step-up.
- Pairing interception: PKCE/device-code hashing, short expiry, single use,
  human approval, and organisation binding.
- Cloud task tampering: canonical Ed25519 signatures, key ids, expiry, nonce,
  and audience checks.
- Replay/reordering: durable ids, monotonic sequence, nonce consumption, bounded
  replay, and resume.
- Workspace escape: canonical root resolution plus parent/symlink checks.
- Command injection: argv execution without a shell, catalogue allowlists,
  exact plan hash, expiry, and no implicit sudo.
- Billing surprise: distinct billing modes and `ASK` paid-fallback default.
- Secret exfiltration: local credential ownership and two-stage redaction.
- Cross-tenant access: organisation checks on every resource.

The reference repository has not undergone an independent security audit.
