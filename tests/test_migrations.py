from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

REPOSITORY = Path(__file__).resolve().parents[1]
ALEMBIC = REPOSITORY / ".venv" / "bin" / "alembic"


def migrate(database_url: str, *arguments: str) -> None:
    environment = os.environ | {"JOYMESH_DATABASE_URL": database_url}
    subprocess.run(
        [str(ALEMBIC), *arguments],
        cwd=REPOSITORY,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_migrations_upgrade_downgrade_and_schema(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}"
    migrate(database_url, "upgrade", "head")
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    await engine.dispose()
    assert {
        "users",
        "nodes",
        "onboarding_progress",
        "action_plans",
        "remote_tasks",
        "audit_events",
    }.issubset(tables)

    migrate(database_url, "downgrade", "base")
    migrate(database_url, "upgrade", "head")
    migrate(database_url, "check")
