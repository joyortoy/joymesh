# Security Release Audit

## Controls verified

| Control | Status |
|---------|--------|
| Private keys never enter JoyCLI | Pass — public key registry only |
| Unsigned mode default off | Pass — `JOYCLI_RUNTIME_ALLOW_UNSIGNED` default false |
| Durable composition blocks unsigned env | Pass — CompositionError |
| Signature required before ACK | Pass — verify then durable commit |
| Tenant/org binding | Pass — organisation allowlist |
| Privacy rejection | Pass — forbidden keys rejected |
| Socket not trusted alone | Pass — publisher auth still required |
| Wheel excludes secrets/pycache | Pass after packaging fix |
| `pip-audit` (JoyCLI crypto pin) | No known vulnerabilities found |
| `pip-audit` (JoyMesh env) | See `reports/pip-audit-joymesh.txt` |

## Residual risks

1. Host compromise with JoyMesh private key allows forged runtime facts.
2. Operator key distribution is manual.
3. JoyMesh hatchling wheels are not claimed bit-reproducible across machines.
4. Cargo audit N/A (no Rust workspace in these packages; cryptography ships its own audited rust extension).

## Deprecated intake

JoyMesh `delivery.intake` remains deprecated/test-only and emits DeprecationWarning; production docs and CLI point to `joyctl runtime intake-serve`.
