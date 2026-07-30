"""Static architecture guards for harness production boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS, HarnessRegistry

SRC = Path(__file__).resolve().parents[1] / "src" / "joymesh"


def test_production_registry_default_excludes_forbidden_ids() -> None:
    registry = HarnessRegistry()
    ids = {item.id for item in registry.definitions()} | {
        adapter.manifest.harness_id for adapter in registry.list()
    }
    assert ids.isdisjoint(FORBIDDEN_PRODUCTION_HARNESS_IDS)


def test_builtin_catalogue_excludes_forbidden_ids() -> None:
    assert {item.id for item in builtin_catalogue()}.isdisjoint(FORBIDDEN_PRODUCTION_HARNESS_IDS)


def test_production_modules_do_not_default_construct_fake_adapter() -> None:
    """AST guard: production packages must not instantiate FakeHarnessAdapter as default."""

    offenders: list[str] = []
    skip = {
        SRC / "adapters" / "fake.py",
        SRC / "fake_worker.py",
    }
    for path in SRC.rglob("*.py"):
        if path in skip or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "FakeHarnessAdapter":
                    # Allowed only if clearly under an explicit adapters= kw in tests —
                    # production default path must not call it.
                    offenders.append(f"{path}:{node.lineno}")
    # adapters/fake.py defines the class; instantiation in production code is forbidden.
    production_offenders = [
        item
        for item in offenders
        if "/adapters/fake.py" not in item and "fake_worker" not in item
    ]
    assert production_offenders == []


def test_harness_registry_source_has_no_fake_in_default_tuple() -> None:
    text = (SRC / "harnesses" / "registry.py").read_text(encoding="utf-8")
    assert "FakeHarnessAdapter()" not in text


def test_custom_harness_module_never_uses_shell_true() -> None:
    path = SRC / "harnesses" / "nonstandard.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            if isinstance(node.value, ast.Constant) and node.value.value is True:
                raise AssertionError("shell=True forbidden in custom harness module")
    # Ignore docstring mentions; assert no real keyword usage above.
    assert "create_subprocess_shell" not in path.read_text(encoding="utf-8")


def test_canonical_registry_is_single_source_for_builtin_ids() -> None:
    from joymesh.harnesses.catalogue import builtin_catalogue
    from joymesh.harnesses.registry import HarnessRegistry

    catalogue_ids = {item.id for item in builtin_catalogue()}
    registry_ids = {item.id for item in HarnessRegistry().definitions()}
    assert catalogue_ids == registry_ids
    assert catalogue_ids.isdisjoint(FORBIDDEN_PRODUCTION_HARNESS_IDS)
