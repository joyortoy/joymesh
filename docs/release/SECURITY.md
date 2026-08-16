# JoyCLI Security

## Defaults

* `JOYCLI_RUNTIME_ALLOW_UNSIGNED=false` (secure)
* Durable production composition refuses unsigned mode
* Private keys never stored in JoyCLI
* Signature verification uses `cryptography` Ed25519
* Tenant/organisation binding enforced on intake
* Privacy allowlist rejects prompts, code, credentials, paths

## Key handling

* JoyMesh: private key via env or `0600` file
* JoyCLI: public key registry only
* Rotate by registering a new `key_id`, then revoking the old key

## Residual risks (accepted for RC1)

* Local Unix socket access still requires publisher authentication; compromised host processes with the private key can publish.
* Key distribution is operator-managed (no automated KMS integration in RC1).
* Dependency audit tooling may be unavailable in offline environments.
