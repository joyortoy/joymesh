"""add evidence trust and execution origin

Revision ID: f9a2b3c4d5e6
Revises: e8f1a2b3c4d5
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a2b3c4d5e6"
down_revision: str | None = "e8f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_evidence") as batch:
        batch.add_column(
            sa.Column(
                "trust_level",
                sa.String(length=40),
                nullable=False,
                server_default="development",
            )
        )
        batch.add_column(
            sa.Column(
                "execution_origin",
                sa.String(length=40),
                nullable=False,
                server_default="inline_development",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("connector_evidence") as batch:
        batch.drop_column("execution_origin")
        batch.drop_column("trust_level")
