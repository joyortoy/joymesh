"""Add provider-neutral execution routing fields to runtime_tasks.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runtime_tasks", sa.Column("selected_backend_id", sa.String(length=100)))
    op.add_column("runtime_tasks", sa.Column("selected_harness_id", sa.String(length=100)))
    op.add_column("runtime_tasks", sa.Column("execution_id", sa.String(length=80)))
    op.add_column("runtime_tasks", sa.Column("execution_decision_reason", sa.String(length=300)))
    op.add_column(
        "runtime_tasks",
        sa.Column(
            "execution_fallback_order_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "runtime_tasks",
        sa.Column(
            "provider_routing_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("runtime_tasks", "provider_routing_required")
    op.drop_column("runtime_tasks", "execution_fallback_order_json")
    op.drop_column("runtime_tasks", "execution_decision_reason")
    op.drop_column("runtime_tasks", "execution_id")
    op.drop_column("runtime_tasks", "selected_harness_id")
    op.drop_column("runtime_tasks", "selected_backend_id")
