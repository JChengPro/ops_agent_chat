"""add agent worker heartbeats

Revision ID: 2f6ad90c11d8
Revises: 7c19d8b42a1f
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f6ad90c11d8"
down_revision: Union[str, None] = "7c19d8b42a1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_audit_events_previous_event_hash"), "audit_events", ["previous_event_hash"], unique=False)
    op.create_table(
        "agent_workers",
        sa.Column("id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_workers_last_seen_at"), "agent_workers", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_workers_last_seen_at"), table_name="agent_workers")
    op.drop_table("agent_workers")
    op.drop_index(op.f("ix_audit_events_previous_event_hash"), table_name="audit_events")
