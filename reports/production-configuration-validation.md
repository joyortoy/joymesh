# Production configuration validation

Commands:

```bash
joyctl production validate-config
joymesh production validate-config
```

Observed unit coverage:

* rejects unsigned mode in production
* requires active publisher keys in production (JoyCLI)
* requires signing key in production (JoyMesh)
* redacts secrets in config dumps

E2E validate-config against a full Linux host profile: pending.
