# Dependency Audit

## JoyCLI

| Class | Packages |
|-------|----------|
| Runtime | `cryptography>=42,<51` (Ed25519 verify) |
| Standard library | `asyncio`, `socket`, `sqlite3`, `json`, `hashlib`, `threading`, `pathlib`, … |
| Development / test | `pytest>=8,<10` (optional extras `dev` / `test`) |
| Build | none (stdlib `joycli_build_backend`) |

### Findings

* **Release defect fixed:** cryptography was imported for production signature verification but not declared in wheel METADATA / `pyproject.toml`. Now declared as a required runtime dependency and emitted as `Requires-Dist`.
* SQLite and Unix sockets are stdlib — no third-party packages required.
* No other third-party imports under `joycli.runtime.intake`.

## JoyMesh

| Class | Packages |
|-------|----------|
| Runtime | `cryptography`, `pydantic`, `sqlalchemy[asyncio]`, `aiosqlite`, `alembic`, `fastapi`, `typer`, `uvicorn`, `websockets` |
| Development | `pytest`, `pytest-asyncio`, `httpx`, `mypy`, `ruff` |

### Findings

* Signing uses existing `cryptography` Ed25519 helpers already declared in JoyMesh metadata.
* Delivery path adds no new undeclared dependencies.

## Graphs (conceptual)

```text
joycli → cryptography → (cffi / openssl bindings via cryptography wheel)
joymesh → cryptography
       → pydantic / sqlalchemy / aiosqlite / alembic / fastapi / uvicorn / typer / websockets
```

## Residual

* Operators must install wheels with dependency resolution (`pip install package.whl`), not `--no-deps`, for production.
* Offline installs must vendor the cryptography wheel for the target platform.
