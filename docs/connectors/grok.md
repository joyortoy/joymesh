# Grok Build connector

Runtime `connector_id`: `grok`  
Display name: **Grok Build**  
Catalogue harness id: `grok`

## Install

```bash
npm install -g @xai-official/grok
# alternate: curl -fsSL https://grok.com/build/install.sh | bash
```

Authenticate with `grok login` (device code / browser). Do not put API keys in argv.

## Live test

```bash
python -m joymesh.cli connector live-test grok --json \
  --workspace <isolated-workspace> \
  --timeout-seconds 180 \
  --prompt "<bounded read-only prompt>"
```

Opt-in pytest gate: `JOYMESH_LIVE_GROK=1`.

## Certification notes

* Uses `--sandbox strict` and `--permission-mode plan` with a read-only tool allowlist.
* Never enables `--always-approve` / `--yolo`.
* Telemetry env vars are forced off for certification runs.
* ACP (`grok agent stdio`) is declared as a capability only; execution stays on CLI subprocess.
* See `docs/runtime/connector-protocol.md` for full denial and privacy semantics.
