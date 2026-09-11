# FireConnect routing

FireConnect is optional and separately approved. It is read-only during initial
detection. A connection plan names the exact harness, model, endpoint,
configuration target, previous value, new value, rollback operation, expiry,
and plan hash.

Applying a transform invalidates certification evidence tied to the previous
provider configuration. Rollback restores the captured prior configuration and
also requires re-certification.

FireConnect never changes a subscription route into paid inference silently.
The normal paid-route policy still applies.
