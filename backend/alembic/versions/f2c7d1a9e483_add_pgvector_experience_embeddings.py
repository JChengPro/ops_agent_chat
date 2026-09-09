"""add pgvector experience embeddings

Revision ID: f2c7d1a9e483
Revises: e6b4a2d9c731
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "f2c7d1a9e483"
down_revision: Union[str, None] = "e6b4a2d9c731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("experience_chunks", sa.Column("embedding", Vector(1536), nullable=True))
    op.execute(
        "CREATE INDEX ix_experience_chunks_embedding_hnsw ON experience_chunks "
        "USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_experience_chunks_embedding_hnsw", table_name="experience_chunks")
    op.drop_column("experience_chunks", "embedding")
