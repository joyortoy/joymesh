"""Add version-aware harness certification evidence.

Revision ID: a12d98d77c31
Revises: 73bb3a1966b0
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a12d98d77c31"
down_revision: str | None = "73bb3a1966b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certification_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("harness_id", sa.String(length=100), nullable=False),
        sa.Column("adapter_version", sa.String(length=50), nullable=False),
        sa.Column("binary_version", sa.String(length=300), nullable=True),
        sa.Column("executable", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_certification_evidence_harness_id"),
        "certification_evidence",
        ["harness_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_certification_evidence_harness_id"),
        table_name="certification_evidence",
    )
    op.drop_table("certification_evidence")
