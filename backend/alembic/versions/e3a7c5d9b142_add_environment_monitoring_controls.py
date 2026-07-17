"""add environment monitoring controls

Revision ID: e3a7c5d9b142
Revises: d7f2a9c4e681
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3a7c5d9b142"
down_revision: Union[str, None] = "d7f2a9c4e681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("monitoring_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "environments",
        sa.Column("auto_remediation_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("environments", "auto_remediation_enabled")
    op.drop_column("environments", "monitoring_enabled")
