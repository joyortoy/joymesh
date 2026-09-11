"""Built-in harness adapters.

``FakeHarnessAdapter`` lives in ``joymesh.adapters.fake`` for **tests only**.
It is not registered in the production harness registry.
"""

from joymesh.adapters.base import HarnessAdapter
from joymesh.adapters.codex import CodexAdapter
from joymesh.adapters.opencode import OpenCodeAdapter

__all__ = ["CodexAdapter", "HarnessAdapter", "OpenCodeAdapter"]
