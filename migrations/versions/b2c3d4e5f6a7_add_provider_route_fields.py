"""Add provider-route selection fields to runtime tasks.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runtime_tasks") as batch:
        batch.add_column(
            sa.Column(
                "preferred_providers_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
        batch.add_column(sa.Column("required_provider", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("selected_provider_id", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column("selected_provider_route_id", sa.String(length=200), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "selected_provider_route_manager_id",
                sa.String(length=100),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("selected_model_id", sa.String(length=300), nullable=True))
        batch.add_column(
            sa.Column("provider_selection_reason", sa.String(length=200), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("runtime_tasks") as batch:
        batch.drop_column("provider_selection_reason")
        batch.drop_column("selected_model_id")
        batch.drop_column("selected_provider_route_manager_id")
        batch.drop_column("selected_provider_route_id")
        batch.drop_column("selected_provider_id")
        batch.drop_column("required_provider")
        batch.drop_column("preferred_providers_json")
