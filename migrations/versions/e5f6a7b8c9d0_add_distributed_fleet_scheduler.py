"""Add distributed fleet scheduler tables.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_workers",
        sa.Column("worker_id", sa.String(length=100), primary_key=True),
        sa.Column("organisation_id", sa.String(length=100)),
        sa.Column("node_id", sa.String(length=100)),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("region", sa.String(length=100)),
        sa.Column("version", sa.String(length=100)),
        sa.Column("runtime", sa.String(length=100)),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("capacity_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("available_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("harnesses_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("providers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("labels_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("running_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("health_detail", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("drain_requested", sa.String(length=8), nullable=False, server_default="0"),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_fleet_workers_organisation_id", "fleet_workers", ["organisation_id"])
    op.create_index("ix_fleet_workers_node_id", "fleet_workers", ["node_id"])
    op.create_index("ix_fleet_workers_state", "fleet_workers", ["state"])
    op.create_index("ix_fleet_workers_region", "fleet_workers", ["region"])

    op.create_table(
        "fleet_heartbeats",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fleet_heartbeats_worker_id", "fleet_heartbeats", ["worker_id"])
    op.create_index("ix_fleet_heartbeats_observed_at", "fleet_heartbeats", ["observed_at"])

    op.create_table(
        "fleet_queue_items",
        sa.Column("queue_id", sa.String(length=80), primary_key=True),
        sa.Column("execution_id", sa.String(length=100), nullable=False),
        sa.Column("mission_id", sa.String(length=100), nullable=False),
        sa.Column("organisation_id", sa.String(length=100)),
        sa.Column("project_id", sa.String(length=100)),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("active_marker", sa.String(length=16)),
        sa.Column("enqueue_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("aging_boost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("claimed_by", sa.String(length=100)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "execution_id", "active_marker", name="uq_active_fleet_queue_execution"
        ),
    )
    op.create_index("ix_fleet_queue_items_execution_id", "fleet_queue_items", ["execution_id"])
    op.create_index("ix_fleet_queue_items_mission_id", "fleet_queue_items", ["mission_id"])
    op.create_index(
        "ix_fleet_queue_items_organisation_id", "fleet_queue_items", ["organisation_id"]
    )
    op.create_index("ix_fleet_queue_items_project_id", "fleet_queue_items", ["project_id"])
    op.create_index("ix_fleet_queue_items_status", "fleet_queue_items", ["status"])
    op.create_index("ix_fleet_queue_items_enqueue_at", "fleet_queue_items", ["enqueue_at"])

    op.create_table(
        "fleet_worker_leases",
        sa.Column("lease_id", sa.String(length=80), primary_key=True),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("execution_id", sa.String(length=100), nullable=False),
        sa.Column("attempt_id", sa.String(length=100), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("active_marker", sa.String(length=16)),
        sa.Column("organisation_id", sa.String(length=100)),
        sa.Column("harness_id", sa.String(length=100)),
        sa.Column("placement_id", sa.String(length=80)),
        sa.Column("reserved_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "active_marker", name="uq_active_fleet_worker_lease"),
    )
    op.create_index("ix_fleet_worker_leases_worker_id", "fleet_worker_leases", ["worker_id"])
    op.create_index("ix_fleet_worker_leases_execution_id", "fleet_worker_leases", ["execution_id"])
    op.create_index("ix_fleet_worker_leases_attempt_id", "fleet_worker_leases", ["attempt_id"])
    op.create_index("ix_fleet_worker_leases_status", "fleet_worker_leases", ["status"])
    op.create_index("ix_fleet_worker_leases_expires_at", "fleet_worker_leases", ["expires_at"])

    op.create_table(
        "fleet_placements",
        sa.Column("placement_id", sa.String(length=80), primary_key=True),
        sa.Column("execution_id", sa.String(length=100), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fleet_placements_execution_id", "fleet_placements", ["execution_id"])
    op.create_index("ix_fleet_placements_worker_id", "fleet_placements", ["worker_id"])

    op.create_table(
        "fleet_scheduler_leaders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_marker", sa.String(length=16)),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("active_marker", name="uq_fleet_scheduler_leader_active"),
    )
    op.create_index("ix_fleet_scheduler_leaders_owner_id", "fleet_scheduler_leaders", ["owner_id"])
    op.create_index(
        "ix_fleet_scheduler_leaders_expires_at", "fleet_scheduler_leaders", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("fleet_scheduler_leaders")
    op.drop_table("fleet_placements")
    op.drop_table("fleet_worker_leases")
    op.drop_table("fleet_queue_items")
    op.drop_table("fleet_heartbeats")
    op.drop_table("fleet_workers")
