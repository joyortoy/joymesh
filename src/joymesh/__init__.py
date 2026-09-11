"""JoyMesh public package.

Heavy service imports are lazy so ``joymesh.sdk`` can be consumed without
pulling connector/runtime implementation dependencies at import time.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApprovalToken",
    "HarnessDefinition",
    "HarnessInstallation",
    "JoyMesh",
    "LifecyclePlan",
    "RunRequest",
]


def __getattr__(name: str) -> Any:
    if name in {
        "ApprovalToken",
        "HarnessDefinition",
        "HarnessInstallation",
        "LifecyclePlan",
    }:
        from joymesh import harnesses as _harnesses

        return getattr(_harnesses, name)
    if name == "RunRequest":
        from joymesh.models import RunRequest

        return RunRequest
    if name == "JoyMesh":
        from joymesh.service import JoyMesh

        return JoyMesh
    raise AttributeError(f"module 'joymesh' has no attribute {name!r}")
