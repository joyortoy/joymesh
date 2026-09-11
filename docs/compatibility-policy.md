# Compatibility Policy

## Channels

* **stable** — production tags only after explicit approval
* **release candidate** — `*-rcN` immutable tags
* **development** — feature branches

## Guarantees (tested profile)

| Surface | Policy |
|---------|--------|
| Wire protocol | v1 required; unsupported versions rejected |
| Runtime projection schema | additive fields may appear; unknown fields ignored by consumers where safe |
| JoyCLI intake schema | versioned migrations; future versions fail closed |
| Signing | Ed25519 raw keys; key_id required |
| ExecutionDirective | revision pinning required |

Unsupported combinations must fail clearly via validate-config / intake rejection codes.
