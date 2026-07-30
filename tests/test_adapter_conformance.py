from collections.abc import Callable
from pathlib import Path

import pytest

from joymesh.adapters.codex import CodexAdapter
from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.adapters.opencode import OpenCodeAdapter
from joymesh.harnesses.adapters import DocumentedCLIAdapter
from joymesh.harnesses.catalogue import builtin_catalogue
from tests.conformance import assert_runtime_conformance, assert_static_conformance


@pytest.mark.parametrize(
    ("kind", "factory"),
    [
        ("fake", lambda _path: FakeHarnessAdapter()),
        ("codex", lambda path: CodexAdapter(str(path), conformance_passed=True)),
        ("opencode", lambda path: OpenCodeAdapter(str(path), conformance_passed=True)),
        (
            "claude-code",
            lambda path: _documented_adapter("claude-code", path),
        ),
        (
            "gemini-cli",
            lambda path: _documented_adapter("gemini-cli", path),
        ),
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


def _documented_adapter(harness_id: str, path: Path) -> DocumentedCLIAdapter:
    from joymesh.harnesses.adapters import builtin_documented_adapters

    adapter = next(
        item
        for item in builtin_documented_adapters(builtin_catalogue())
        if item.manifest.harness_id == harness_id
    )
    return DocumentedCLIAdapter(
        adapter.definition,
        adapter._argv_builder,
        executable=str(path),
        conformance_passed=True,
    )
