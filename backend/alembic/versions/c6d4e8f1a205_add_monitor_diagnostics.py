"""add monitor diagnostics

Revision ID: c6d4e8f1a205
Revises: f8c1a6e4d203
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d4e8f1a205"
down_revision: Union[str, None] = "f8c1a6e4d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("monitor_events", sa.Column("diagnostic_run_id", sa.String(length=36), nullable=True))
    op.add_column("monitor_events", sa.Column("diagnosis_summary", sa.Text(), nullable=True))
    op.add_column("monitor_events", sa.Column("diagnosed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_monitor_events_diagnostic_run_id_agent_runs",
        "monitor_events",
        "agent_runs",
        ["diagnostic_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_monitor_events_diagnostic_run_id",
        "monitor_events",
        ["diagnostic_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_monitor_events_diagnostic_run_id", table_name="monitor_events")
    op.drop_constraint("fk_monitor_events_diagnostic_run_id_agent_runs", "monitor_events", type_="foreignkey")
    op.drop_column("monitor_events", "diagnosed_at")
    op.drop_column("monitor_events", "diagnosis_summary")
    op.drop_column("monitor_events", "diagnostic_run_id")
