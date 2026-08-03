# JoyCLI Installation

## Requirements

* Python >= 3.11
* Unix domain sockets (POSIX)
* SQLite (stdlib)
* Network only to install declared dependencies (`cryptography`)

## Install from wheels

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install ./dist/joycli-*.whl
# For end-to-end runtime delivery also install JoyMesh:
python -m pip install /path/to/joymesh-*.whl
```

Verify:

```bash
python -c "import joycli, cryptography; print(joycli.__version__, cryptography.__version__)"
joyctl --version
```

Do not set `PYTHONPATH` to a source checkout for production.

## Key provisioning

JoyCLI stores **public** publisher keys only.

```bash
export JOYCLI_RUNTIME_PUBLISHER_PUBLIC_KEY="<ed25519-public-base64url>"
export JOYCLI_RUNTIME_PUBLISHER_KEY_ID="joymesh-ed25519-1"
export JOYCLI_RUNTIME_ALLOW_UNSIGNED=0   # default secure posture
```

JoyMesh holds the private key:

```bash
export JOYMESH_RUNTIME_SIGNING_KEY="<ed25519-private-base64url>"
# or
export JOYMESH_RUNTIME_SIGNING_KEY_PATH=~/.config/joymesh/runtime.ed25519
export JOYMESH_RUNTIME_SIGNING_KEY_ID="joymesh-ed25519-1"
```

## Start runtime intake

```bash
joyctl --mode durable-local --state /var/lib/joycli \
  runtime intake-serve --socket "$XDG_RUNTIME_DIR/joymesh-delivery.sock"
```
