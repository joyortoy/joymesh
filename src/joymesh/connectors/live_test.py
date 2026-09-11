"""Deprecated Cursor live-test helper — use runtime_v1.connectors.live_test."""

from __future__ import annotations

from joymesh.runtime_v1.connectors.live_test import (
    render_live_test_result,
    run_connector_live_test,
    run_cursor_live_test,
)

__all__ = [
    "render_live_test_result",
    "run_connector_live_test",
    "run_cursor_live_test",
]
