# Production Security

* Unsigned production intake impossible when `JOYCLI_ENV=production`
* JoyMesh production refuses ephemeral signing keys
* Private keys never stored in JoyCLI
* Key files mode 0600
* systemd: NoNewPrivileges, ProtectSystem, PrivateTmp, UMask=0077
* Tenant organisation binding on publisher keys
* Metrics/logs redact secrets

Residual risks: operator-managed keys (no KMS), single-node SQLite, Unix-socket host trust boundary.
