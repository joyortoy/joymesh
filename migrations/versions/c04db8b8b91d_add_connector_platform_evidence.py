"""add connector platform evidence

Revision ID: c04db8b8b91d
Revises: 7f6769cf78ae
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c04db8b8b91d"
down_revision: str | None = "7f6769cf78ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_definition_revisions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("revision", sa.String(100), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "node_connector_discoveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(100), nullable=False),
        sa.Column("executable", sa.Text),
        sa.Column("version", sa.String(300)),
        sa.Column("execution_environment", sa.String(30), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "node_connector_installations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(100), nullable=False),
        sa.Column("method_id", sa.String(100), nullable=False),
        sa.Column("executable", sa.Text, nullable=False),
        sa.Column("version", sa.String(300)),
        sa.Column("enabled_for_routing", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "node_connector_authentication",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("method_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("detail", sa.Text),
    )
    op.create_table(
        "node_connector_provider_modes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("funding_source", sa.String(40), nullable=False),
        sa.Column("separately_billed", sa.Boolean),
        sa.Column("configuration_status", sa.String(40), nullable=False),
    )
    op.create_table(
        "node_connector_certifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(100), nullable=False),
        sa.Column("harness_version", sa.String(300), nullable=False),
        sa.Column("executable_fingerprint", sa.String(128), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("passed_levels_json", sa.Text, nullable=False),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "connector_task_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(100), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("plan_json", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "connector_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("connector_task_plans.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("node_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("detail", sa.Text),
    )
    op.create_table(
        "connector_capability_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "certification_id",
            sa.String(36),
            sa.ForeignKey("node_connector_certifications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("declared", sa.Boolean, nullable=False),
        sa.Column("observed", sa.Boolean, nullable=False),
        sa.Column("certified", sa.Boolean, nullable=False),
    )
    op.create_table(
        "connector_official_source_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(100), nullable=False, index=True),
        sa.Column("connector_revision", sa.String(100), nullable=False),
        sa.Column("documentation_source", sa.Text, nullable=False),
        sa.Column("source_repository", sa.Text),
        sa.Column("package_source", sa.Text),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("installation_method_fingerprint", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "connector_official_source_records",
        "connector_capability_evidence",
        "connector_tasks",
        "connector_task_plans",
        "node_connector_certifications",
        "node_connector_provider_modes",
        "node_connector_authentication",
        "node_connector_installations",
        "node_connector_discoveries",
        "connector_definition_revisions",
    ):
        op.drop_table(table)
