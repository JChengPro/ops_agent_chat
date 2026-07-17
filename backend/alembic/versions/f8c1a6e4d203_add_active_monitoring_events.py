"""add active monitoring events

Revision ID: f8c1a6e4d203
Revises: e3a7c5d9b142
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8c1a6e4d203"
down_revision: Union[str, None] = "e3a7c5d9b142"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("environments", sa.Column("last_monitored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("environments", sa.Column("next_monitor_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_environments_next_monitor_at", "environments", ["next_monitor_at"], unique=False)
    op.create_table(
        "monitor_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("service_name", sa.String(length=255), nullable=False),
        sa.Column("issue_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("remediation_action_id", sa.String(length=36), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('open','remediating','remediated','resolved','remediation_failed')",
            name="ck_monitor_event_status",
        ),
        sa.CheckConstraint("severity IN ('info','warning','critical')", name="ck_monitor_event_severity"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["remediation_action_id"], ["actions.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitor_events_environment_id", "monitor_events", ["environment_id"], unique=False)
    op.create_index("ix_monitor_events_issue_type", "monitor_events", ["issue_type"], unique=False)
    op.create_index("ix_monitor_events_project_id", "monitor_events", ["project_id"], unique=False)
    op.create_index("ix_monitor_events_run_id", "monitor_events", ["run_id"], unique=False)
    op.create_index("ix_monitor_events_service_name", "monitor_events", ["service_name"], unique=False)
    op.create_index("ix_monitor_events_status", "monitor_events", ["status"], unique=False)
    op.create_index(
        "uq_monitor_event_active_issue",
        "monitor_events",
        ["environment_id", "service_name", "issue_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('open','remediating','remediation_failed')"),
    )


def downgrade() -> None:
    op.drop_index("uq_monitor_event_active_issue", table_name="monitor_events", postgresql_where=sa.text("status IN ('open','remediating','remediation_failed')"))
    op.drop_index("ix_monitor_events_status", table_name="monitor_events")
    op.drop_index("ix_monitor_events_service_name", table_name="monitor_events")
    op.drop_index("ix_monitor_events_run_id", table_name="monitor_events")
    op.drop_index("ix_monitor_events_project_id", table_name="monitor_events")
    op.drop_index("ix_monitor_events_issue_type", table_name="monitor_events")
    op.drop_index("ix_monitor_events_environment_id", table_name="monitor_events")
    op.drop_table("monitor_events")
    op.drop_index("ix_environments_next_monitor_at", table_name="environments")
    op.drop_column("environments", "next_monitor_at")
    op.drop_column("environments", "last_monitored_at")
