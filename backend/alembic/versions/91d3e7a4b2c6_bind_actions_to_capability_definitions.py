"""bind actions to exact capability definitions

Revision ID: 91d3e7a4b2c6
Revises: 2f6ad90c11d8
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91d3e7a4b2c6"
down_revision: Union[str, None] = "2f6ad90c11d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("capability_definition_hash", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE actions AS action
        SET capability_definition_hash = version.definition_hash
        FROM capability_versions AS version
        WHERE version.name = action.capability_name
          AND version.version = action.capability_version
        """
    )
    # An unmatched legacy Action must never become executable after migration.
    op.execute(
        """
        UPDATE actions
        SET capability_definition_hash = repeat('0', 64)
        WHERE capability_definition_hash IS NULL
        """
    )
    op.alter_column("actions", "capability_definition_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("actions", "capability_definition_hash")
