from collections.abc import Callable
from pathlib import Path

import pytest

from joymesh.adapters.codex import CodexAdapter
from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.adapters.opencode import OpenCodeAdapter
from tests.conformance import assert_runtime_conformance, assert_static_conformance


@pytest.mark.parametrize(
    ("kind", "factory"),
    [
        ("fake", lambda _path: FakeHarnessAdapter()),
        ("codex", lambda path: CodexAdapter(str(path), conformance_passed=True)),
        ("opencode", lambda path: OpenCodeAdapter(str(path), conformance_passed=True)),
    ],
)
async def test_adapter_conformance(
    kind: str,
    factory: Callable,
    fake_executable_factory,
    tmp_path: Path,
) -> None:
    executable = fake_executable_factory(kind)
    adapter = factory(executable)
    await assert_static_conformance(adapter, tmp_path)
    await assert_runtime_conformance(
        adapter,
        tmp_path,
        f"sqlite+aiosqlite:///{tmp_path / f'{kind}.db'}",
    )
