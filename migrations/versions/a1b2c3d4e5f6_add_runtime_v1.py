"""add joy mesh runtime v1 tables

Revision ID: a1b2c3d4e5f6
Revises: f9a2b3c4d5e6
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f9a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("prompt_digest", sa.String(length=128), nullable=False),
        sa.Column("prompt_size", sa.Integer(), nullable=False),
        sa.Column("requested_capabilities_json", sa.Text(), nullable=False),
        sa.Column("prohibited_capabilities_json", sa.Text(), nullable=False),
        sa.Column("expanded_capabilities_json", sa.Text(), nullable=False),
        sa.Column("policy_profile", sa.String(length=80), nullable=False),
        sa.Column("preferred_connectors_json", sa.Text(), nullable=False),
        sa.Column("required_connector", sa.String(length=100)),
        sa.Column("preferred_nodes_json", sa.Text(), nullable=False),
        sa.Column("required_node", sa.String(length=100)),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("selected_node_id", sa.String(length=100)),
        sa.Column("selected_connector_id", sa.String(length=100)),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runtime_tasks_workspace_id", "runtime_tasks", ["workspace_id"])
    op.create_index("ix_runtime_tasks_user_id", "runtime_tasks", ["user_id"])
    op.create_index("ix_runtime_tasks_status", "runtime_tasks", ["status"])

    op.create_table(
        "route_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("scheduling_round", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("policy_profile", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("rejection_reasons_json", sa.Text(), nullable=False),
        sa.Column("scoring_factors_json", sa.Text(), nullable=False),
        sa.Column("certified_capabilities_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id",
            "scheduling_round",
            "node_id",
            "connector_id",
            name="uq_route_candidate",
        ),
    )
    op.create_index("ix_route_candidates_task_id", "route_candidates", ["task_id"])

    op.create_table(
        "task_leases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("active_marker", sa.String(length=36)),
        sa.UniqueConstraint("task_id", "active_marker", name="uq_active_lease_per_task"),
    )
    op.create_index("ix_task_leases_task_id", "task_leases", ["task_id"])
    op.create_index("ix_task_leases_attempt_id", "task_leases", ["attempt_id"])

    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("lease_id", sa.String(length=36), nullable=False),
        sa.Column("execution_origin", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("failure_class", sa.String(length=40)),
        sa.Column("retry_safe", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("task_id", "attempt_number", name="uq_attempt_number"),
    )
    op.create_index("ix_execution_attempts_task_id", "execution_attempts", ["task_id"])

    op.create_table(
        "workspace_placements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=100), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("writable", sa.Boolean(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expose_path", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_workspace_placements_workspace_id", "workspace_placements", ["workspace_id"]
    )
    op.create_index("ix_workspace_placements_node_id", "workspace_placements", ["node_id"])

    op.create_table(
        "runtime_audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36)),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runtime_audit_events_task_id", "runtime_audit_events", ["task_id"])
    op.create_index("ix_runtime_audit_events_event_type", "runtime_audit_events", ["event_type"])

    op.create_table(
        "certified_capabilities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("capability_id", sa.String(length=120), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("certification_profile", sa.String(length=80), nullable=False),
        sa.Column("certification_profile_revision", sa.String(length=40), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("execution_origin", sa.String(length=40), nullable=False),
        sa.Column("trust_level", sa.String(length=40), nullable=False),
        sa.Column("executable_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("connector_revision", sa.String(length=100), nullable=False),
        sa.Column("connector_version", sa.String(length=300), nullable=False),
        sa.Column("capability_definition_revision", sa.String(length=40), nullable=False),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("invalidation_reason", sa.Text()),
        sa.UniqueConstraint(
            "evidence_id", "capability_id", name="uq_certified_capability_evidence"
        ),
    )
    op.create_index(
        "ix_certified_capabilities_capability_id", "certified_capabilities", ["capability_id"]
    )
    op.create_index(
        "ix_certified_capabilities_connector_id", "certified_capabilities", ["connector_id"]
    )
    op.create_index("ix_certified_capabilities_node_id", "certified_capabilities", ["node_id"])
    op.create_index(
        "ix_certified_capabilities_evidence_id", "certified_capabilities", ["evidence_id"]
    )


def downgrade() -> None:
    op.drop_table("certified_capabilities")
    op.drop_table("runtime_audit_events")
    op.drop_table("workspace_placements")
    op.drop_table("execution_attempts")
    op.drop_table("task_leases")
    op.drop_table("route_candidates")
    op.drop_table("runtime_tasks")
