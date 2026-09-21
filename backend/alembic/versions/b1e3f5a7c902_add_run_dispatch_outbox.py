"""Versioned, transactional AgentRun delivery."""
from alembic import op
import sqlalchemy as sa

revision = "b1e3f5a7c902"
down_revision = "a9d2e7f4b610"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("dispatch_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "agent_run_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dispatch_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(120)),
        sa.UniqueConstraint("run_id", "dispatch_version", name="uq_run_outbox_dispatch"),
    )
    op.create_index("ix_run_outbox_next_attempt", "agent_run_outbox", ["next_attempt_at"])
    op.execute("""INSERT INTO agent_run_outbox (id,run_id,dispatch_version,attempts)
                  SELECT gen_random_uuid()::text,id,dispatch_version,0 FROM agent_runs WHERE status='queued'""")


def downgrade():
    op.drop_table("agent_run_outbox")
    op.drop_column("agent_runs", "dispatch_version")
