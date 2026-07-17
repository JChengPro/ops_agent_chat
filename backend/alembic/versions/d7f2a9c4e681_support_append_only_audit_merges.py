"""support append-only audit-chain merges

Revision ID: d7f2a9c4e681
Revises: c4e8b1f6a205
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7f2a9c4e681"
down_revision: Union[str, None] = "c4e8b1f6a205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("parent_event_hashes_json", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column(
        "audit_events",
        sa.Column("hash_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "hash_version")
    op.drop_column("audit_events", "parent_event_hashes_json")
