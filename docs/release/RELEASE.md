# JoyCLI Release

## Version

Package version is defined in `src/joycli/__init__.py` (`__version__`).

Current candidate: **0.26.0** (RC1 packaging track).

## Build

```bash
python -c "from joycli_build_backend import build_wheel, build_sdist; print(build_wheel('dist')); print(build_sdist('dist'))"
```

Wheel METADATA must include:

```text
Requires-Dist: cryptography>=42,<51
```

## Release gates

1. `python -m compileall -q src tests`
2. `pytest -q`
3. Integrity manifest regeneration matches committed file
4. Clean wheel install with JoyMesh (`scripts/verify_clean_wheel_install.sh` from JoyMesh repo)
5. Cross-repo signed intake, routing E2E, crash recovery, fresh-install ritual

## What is not included in RC1

* JoyClaw UI
* Hosted multi-tenant key distribution service
* Automatic private-key rotation orchestration
