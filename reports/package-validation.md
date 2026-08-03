# Package Validation

## Artifacts (post-packaging fix)

| Package | Filename | Version | Size (bytes) | SHA-256 |
|---------|----------|---------|-------------:|---------|
| JoyCLI | `joycli-0.26.0-py3-none-any.whl` | 0.26.0 | 242982 | `f0ed5f6fe1d5c5409088ac7be6a0a775ba20ae9e1743e6181b8ff1d55f344503` |
| JoyMesh | `joymesh-0.1.0-py3-none-any.whl` | 0.1.0 | 386142 | `80e882526d763e88451a538f8ee5de53d3d3582816fb3656430bb711a3cef8d2` |

Build backend timestamps:

* JoyCLI wheel ZIP members use fixed date `1980-01-01` (deterministic backend).
* JoyMesh hatchling build timestamp is environment-local (not bit-for-bit reproducible across hosts).

## Wheel content checks

* JoyCLI wheel excludes `__pycache__` (packaging defect fixed).
* JoyCLI METADATA includes `Requires-Dist: cryptography>=42,<51`.
* JoyMesh METADATA includes `Requires-Dist: cryptography…` among declared runtime deps.

## Clean install command

```bash
bash scripts/verify_clean_wheel_install.sh
```

Observed: install from wheels only, cryptography resolved, signed intake ACK, eligible routing selected `opencode`, projection survived reopen.
