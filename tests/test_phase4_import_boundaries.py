"""Phase 4: JoyMesh must not import JoyCTL planning/completion/memory internals."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "joymesh"

FORBIDDEN = (
    "joyctl.runtime.completion",
    "joycli.mission",
    "joycli.memory",
    "joycli.planner",
    "joylegal.registries",
    "joypay.providers",
    "joymux.placement.engine",
)


def test_no_forbidden_peer_implementation_imports() -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for name in mods:
                for forbidden in FORBIDDEN:
                    if name == forbidden or name.startswith(forbidden + "."):
                        violations.append(f"{path.relative_to(SRC)}: {name}")
    assert violations == []


def test_no_hardcoded_sibling_checkouts() -> None:
    hits = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "/Users/joytan/" in text:
            hits.append(str(path.relative_to(SRC)))
    assert hits == []
