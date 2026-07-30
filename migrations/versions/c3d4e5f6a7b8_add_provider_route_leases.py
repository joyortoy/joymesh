"""Add provider-route mutation leases.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_route_leases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("manager_id", sa.String(length=100), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("owner_execution_id", sa.String(length=100), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("active_marker", sa.String(length=16), nullable=True),
        sa.Column("original_state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("target_provider_id", sa.String(length=100), nullable=True),
        sa.Column("target_model_id", sa.String(length=300), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_status", sa.String(length=40), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("lease_token", name="uq_provider_route_lease_token"),
        sa.UniqueConstraint(
            "manager_id",
            "connector_id",
            "active_marker",
            name="uq_active_provider_route_lease",
        ),
    )
    op.create_index(
        "ix_provider_route_leases_manager_id",
        "provider_route_leases",
        ["manager_id"],
    )
    op.create_index(
        "ix_provider_route_leases_connector_id",
        "provider_route_leases",
        ["connector_id"],
    )
    op.create_index(
        "ix_provider_route_leases_owner_execution_id",
        "provider_route_leases",
        ["owner_execution_id"],
    )
    op.create_index(
        "ix_provider_route_leases_status",
        "provider_route_leases",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_route_leases_status", table_name="provider_route_leases")
    op.drop_index(
        "ix_provider_route_leases_owner_execution_id",
        table_name="provider_route_leases",
    )
    op.drop_index("ix_provider_route_leases_connector_id", table_name="provider_route_leases")
    op.drop_index("ix_provider_route_leases_manager_id", table_name="provider_route_leases")
    op.drop_table("provider_route_leases")
