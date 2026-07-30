"""extend connector lifecycle task evidence and readiness

Revision ID: e8f1a2b3c4d5
Revises: c04db8b8b91d
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f1a2b3c4d5"
down_revision: str | None = "c04db8b8b91d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_tasks_v2",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(length=36),
            sa.ForeignKey("connector_task_plans.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("node_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False, index=True),
        sa.Column("previous_task_id", sa.String(length=36)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("detail", sa.Text()),
    )
    op.execute(
        """
        INSERT INTO connector_tasks_v2 (
            id, plan_id, node_id, connector_id, connector_revision, action, plan_hash,
            status, idempotency_key, previous_task_id, version, created_at, started_at,
            finished_at, detail
        )
        SELECT
            id, plan_id, node_id, connector_id, '', '', '',
            status, id, NULL, 1,
            COALESCE(started_at, CURRENT_TIMESTAMP), started_at, finished_at, detail
        FROM connector_tasks
        """
    )
    op.drop_table("connector_tasks")
    op.rename_table("connector_tasks_v2", "connector_tasks")
    # SQLite preserves temporary index names across rename; rebuild expected names.
    for name in (
        "ix_connector_tasks_v2_connector_id",
        "ix_connector_tasks_v2_idempotency_key",
        "ix_connector_tasks_v2_node_id",
        "ix_connector_tasks_v2_plan_id",
    ):
        op.execute(f'DROP INDEX IF EXISTS "{name}"')
    op.create_index("ix_connector_tasks_connector_id", "connector_tasks", ["connector_id"])
    op.create_index("ix_connector_tasks_idempotency_key", "connector_tasks", ["idempotency_key"])
    op.create_index("ix_connector_tasks_node_id", "connector_tasks", ["node_id"])
    op.create_index("ix_connector_tasks_plan_id", "connector_tasks", ["plan_id"])

    op.create_table(
        "connector_task_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("connector_tasks.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("node_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "connector_evidence",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("node_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(length=100), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("evidence_type", sa.String(length=40), nullable=False, index=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("executable_path", sa.Text()),
        sa.Column("executable_fingerprint", sa.String(length=128)),
        sa.Column("harness_version", sa.String(length=300)),
        sa.Column("provider_mode", sa.String(length=100)),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "node_connector_readiness",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("node_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("recommended_action", sa.String(length=40)),
        sa.Column("blocking_reason", sa.Text()),
        sa.Column("active_task_id", sa.String(length=36)),
        sa.Column("latest_evidence_id", sa.String(length=36)),
        sa.Column("routing_eligible", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("recomputed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("node_connector_readiness")
    op.drop_table("connector_evidence")
    op.drop_table("connector_task_events")
    op.create_table(
        "connector_tasks_legacy",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(length=36),
            sa.ForeignKey("connector_task_plans.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("node_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("detail", sa.Text()),
    )
    op.execute(
        """
        INSERT INTO connector_tasks_legacy (
            id, plan_id, node_id, connector_id, status, started_at, finished_at, detail
        )
        SELECT id, plan_id, node_id, connector_id, status, started_at, finished_at, detail
        FROM connector_tasks
        """
    )
    op.drop_table("connector_tasks")
    op.rename_table("connector_tasks_legacy", "connector_tasks")
