"""Add independent, best-effort AgentRun performance records."""

from alembic import op
import sqlalchemy as sa

revision = "a9d2e7f4b610"
down_revision = "f2c7d1a9e483"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_run_profile_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_profile_run_started", "agent_run_profile_spans", ["run_id", "started_at"])


def downgrade():
    op.drop_table("agent_run_profile_spans")
