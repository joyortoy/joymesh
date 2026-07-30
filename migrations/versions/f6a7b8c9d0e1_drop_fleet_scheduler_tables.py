"""Drop JoyMesh-owned fleet scheduler tables.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-30

Fleet scheduling moved to JoyCLI. JoyMesh no longer persists authoritative
worker registry, queues, placements, worker leases, or scheduler leadership.
"""

from __future__ import annotations

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("fleet_scheduler_leaders")
    op.drop_table("fleet_placements")
    op.drop_table("fleet_worker_leases")
    op.drop_table("fleet_queue_items")
    op.drop_table("fleet_heartbeats")
    op.drop_table("fleet_workers")


def downgrade() -> None:
    # Recreate empty schema for rollback only — not an active JoyMesh control plane.
    import sqlalchemy as sa

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
    op.create_table(
        "fleet_placements",
        sa.Column("placement_id", sa.String(length=80), primary_key=True),
        sa.Column("execution_id", sa.String(length=100), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
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
