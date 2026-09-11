# Package Validation

## Artifacts (RC1 clean-worktree rebuild)

| Package | Filename | Version | Size (bytes) | SHA-256 |
|---------|----------|---------|-------------:|---------|
| JoyCLI | `joycli-0.26.0-py3-none-any.whl` | 0.26.0 | 242982 | `f0ed5f6fe1d5c5409088ac7be6a0a775ba20ae9e1743e6181b8ff1d55f344503` |
| JoyMesh | `joymesh-0.1.0-py3-none-any.whl` | 0.1.0 | 381934 | `78bf7cda024315c12e86acc520fc519ced9e5b4a3236e60be9aa8e8089044bc0` |

Source commits for these wheels:

* JoyCLI: `5fd55fafaeebd2408b69c8417bcf11321351d587`
* JoyMesh: `c710b7c3492b011fe6b509bce3523768c630a435`

Build:

```bash
python -m build --wheel
# Python 3.12.13; macOS arm64
```

Build backend timestamps:

* JoyCLI wheel ZIP members use fixed date `1980-01-01` (deterministic backend).
* JoyMesh hatchling build timestamp is environment-local (not bit-for-bit reproducible across hosts).

## Wheel content checks

* JoyCLI wheel excludes `__pycache__` / `.pyc`.
* JoyCLI METADATA includes `Requires-Dist: cryptography>=42,<51`.
* JoyMesh METADATA includes declared runtime deps including cryptography.
* JoyMesh wheel includes `joymesh/quota/` and `joymesh/delivery/`.
* No absolute checkout paths or private key material in either wheel.

## Clean install command

```bash
bash scripts/verify_clean_wheel_install.sh
# RC1 evidence used prebuilt wheels from clean worktrees (same SHAs as table).
```

Observed: site-packages only; cryptography resolved; signed intake ACK; route selected `opencode`; projection/directive agreement.
