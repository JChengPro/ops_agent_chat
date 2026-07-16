"""harden execution and authentication state

Revision ID: 7c19d8b42a1f
Revises: 55590888dec3
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c19d8b42a1f"
down_revision: Union[str, None] = "55590888dec3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("resolved_spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("actions", sa.Column("rollback_spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("actions", sa.Column("execution_token", sa.String(length=36), nullable=True))
    op.add_column("actions", sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("actions", sa.Column("execution_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_actions_execution_token"), "actions", ["execution_token"], unique=False)

    op.add_column("agent_runs", sa.Column("lease_owner", sa.String(length=120), nullable=True))
    op.add_column("agent_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_agent_runs_lease_owner"), "agent_runs", ["lease_owner"], unique=False)
    op.create_index(op.f("ix_agent_runs_lease_expires_at"), "agent_runs", ["lease_expires_at"], unique=False)

    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "login_throttles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_key", sa.String(length=400), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_login_throttles_identity_key"), "login_throttles", ["identity_key"], unique=True)

    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY project_id ORDER BY updated_at DESC, id DESC) AS rn
            FROM environments WHERE is_default = true AND is_active = true
        )
        UPDATE environments SET is_default = false
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.create_index(
        "uq_project_active_default_environment",
        "environments",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_project_active_default_environment", table_name="environments")
    op.drop_index(op.f("ix_login_throttles_identity_key"), table_name="login_throttles")
    op.drop_table("login_throttles")
    op.drop_column("users", "token_version")
    op.drop_index(op.f("ix_agent_runs_lease_expires_at"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_lease_owner"), table_name="agent_runs")
    op.drop_column("agent_runs", "cancel_requested_at")
    op.drop_column("agent_runs", "heartbeat_at")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "lease_owner")
    op.drop_index(op.f("ix_actions_execution_token"), table_name="actions")
    op.drop_column("actions", "execution_finished_at")
    op.drop_column("actions", "execution_started_at")
    op.drop_column("actions", "execution_token")
    op.drop_column("actions", "resolved_spec_json")
    op.drop_column("actions", "rollback_spec_json")
