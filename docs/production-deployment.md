# Production Deployment

## Tested profile

```text
Single organisation / controlled multi-tenant
Linux x86-64 (qualification also exercised on macOS arm64 host for packaging)
Unix-socket JoyMesh → JoyCLI runtime-state delivery
Packaged wheels
Ed25519 publisher authentication
SQLite durable runtime intake
OpenCode as verified live harness
```

## Walkthrough (clean host)

1. Install wheels (no source PYTHONPATH):

```bash
python3.12 -m venv /opt/joymux/venv
/opt/joymux/venv/bin/pip install joycli-0.26.0-py3-none-any.whl joymesh-0.1.0-py3-none-any.whl
```

2. Generate signing key (JoyMesh only):

```bash
joymesh runtime key generate --destination /etc/joymesh/keys/runtime.key --key-id prod-1
# Record public_key from JSON output; never log the private key file contents.
chmod 600 /etc/joymesh/keys/runtime.key
```

3. Register public key on JoyCLI:

```bash
joyctl --state /var/lib/joycli runtime publisher-key add \
  --key-id prod-1 --public-key '<public>' --publisher-id joymesh --organisation-id local
```

4. Environment files:

`/etc/joycli/runtime.env`:

```bash
JOYCLI_ENV=production
JOYCLI_RUNTIME_ALLOW_UNSIGNED=0
JOYCLI_RUNTIME_SOCKET=/run/joycli/joymesh-delivery.sock
JOYCLI_RUNTIME_INTAKE_DB=/var/lib/joycli/runtime_intake.sqlite3
JOYCLI_PUBLISHER_KEY_REGISTRY=/var/lib/joycli/publisher_keys.json
```

`/etc/joymesh/runtime.env`:

```bash
JOYMESH_ENV=production
JOYMESH_DELIVERY_TRANSPORT=unix_socket
JOYMESH_DELIVERY_SOCKET=/run/joycli/joymesh-delivery.sock
JOYMESH_RUNTIME_SIGNING_KEY_PATH=/etc/joymesh/keys/runtime.key
JOYMESH_RUNTIME_SIGNING_KEY_ID=prod-1
```

5. Validate (must not start services):

```bash
joyctl --state /var/lib/joycli production validate-config
joymesh production validate-config
```

6. Enable systemd units from `deploy/systemd/` and start JoyCLI intake before JoyMesh publishers.

7. Verify:

```bash
joyctl --state /var/lib/joycli runtime intake-status
joymesh delivery health
```

Expected: ready listener, connected delivery, unsigned mode off.
