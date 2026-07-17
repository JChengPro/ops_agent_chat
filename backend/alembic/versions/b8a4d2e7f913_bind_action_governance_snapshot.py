"""bind action governance snapshot

Revision ID: b8a4d2e7f913
Revises: 91d3e7a4b2c6
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8a4d2e7f913"
down_revision: Union[str, None] = "91d3e7a4b2c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("risk_level", sa.String(length=10), nullable=False, server_default="L3"))
    op.add_column("actions", sa.Column("approval_mode", sa.String(length=30), nullable=False, server_default="forbidden"))
    op.add_column("actions", sa.Column("policy_version", sa.String(length=40), nullable=False, server_default="legacy-unbound"))
    op.add_column("actions", sa.Column("config_revision", sa.String(length=64), nullable=False, server_default="0" * 64))


def downgrade() -> None:
    op.drop_column("actions", "config_revision")
    op.drop_column("actions", "policy_version")
    op.drop_column("actions", "approval_mode")
    op.drop_column("actions", "risk_level")
