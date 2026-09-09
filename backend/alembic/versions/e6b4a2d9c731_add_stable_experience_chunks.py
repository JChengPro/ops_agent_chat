"""add stable experience chunks

Revision ID: e6b4a2d9c731
Revises: b3f7a2c9d104
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6b4a2d9c731"
down_revision: Union[str, None] = "b3f7a2c9d104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("experience_chunks", sa.Column("chunk_key", sa.String(length=64), nullable=True))
    op.add_column("experience_chunks", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("experience_chunks", sa.Column("source_ref", sa.String(length=1000), nullable=True))
    op.add_column("experience_chunks", sa.Column("heading_path", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.add_column("experience_chunks", sa.Column("chunk_index", sa.Integer(), nullable=True))
    op.execute("UPDATE experience_chunks SET chunk_key = md5('legacy:' || id::text) || md5('chunk:' || id::text)")
    op.execute("UPDATE experience_chunks SET content_hash = md5(content) || md5('content:' || content)")
    op.execute("UPDATE experience_chunks SET source_ref = 'experience:' || experience_item_id::text")
    op.execute("UPDATE experience_chunks SET chunk_index = id")
    op.alter_column("experience_chunks", "chunk_key", nullable=False)
    op.alter_column("experience_chunks", "content_hash", nullable=False)
    op.alter_column("experience_chunks", "source_ref", nullable=False)
    op.alter_column("experience_chunks", "chunk_index", nullable=False)
    op.create_index(op.f("ix_experience_chunks_content_hash"), "experience_chunks", ["content_hash"], unique=False)
    op.create_unique_constraint("uq_experience_chunk_key", "experience_chunks", ["experience_item_id", "chunk_key"])


def downgrade() -> None:
    op.drop_constraint("uq_experience_chunk_key", "experience_chunks", type_="unique")
    op.drop_index(op.f("ix_experience_chunks_content_hash"), table_name="experience_chunks")
    op.drop_column("experience_chunks", "chunk_index")
    op.drop_column("experience_chunks", "heading_path")
    op.drop_column("experience_chunks", "source_ref")
    op.drop_column("experience_chunks", "content_hash")
    op.drop_column("experience_chunks", "chunk_key")
