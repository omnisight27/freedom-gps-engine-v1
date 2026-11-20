"""Add the state_vectors table (universal state vector Ω(t)).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20

One row is one snapshot of one user's nine state-vector variables
[F, H, S, R, L, T, M, I, E] at one time, plus the opportunity value.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "state_vectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("financial", sa.Float(), nullable=True),
        sa.Column("human", sa.Float(), nullable=True),
        sa.Column("social", sa.Float(), nullable=True),
        sa.Column("resource", sa.Float(), nullable=True),
        sa.Column("legacy", sa.Float(), nullable=True),
        sa.Column("trust", sa.Float(), nullable=True),
        sa.Column("memory", sa.Float(), nullable=True),
        sa.Column("intelligence", sa.Float(), nullable=True),
        sa.Column("entropy", sa.Float(), nullable=True),
        sa.Column("opportunity", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_state_vectors_user_id", "state_vectors", ["user_id"])
    op.create_index("ix_state_vectors_id", "state_vectors", ["id"])
    op.create_index(
        "ix_state_vectors_recorded_at", "state_vectors", ["recorded_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_state_vectors_recorded_at", table_name="state_vectors")
    op.drop_index("ix_state_vectors_id", table_name="state_vectors")
    op.drop_index("ix_state_vectors_user_id", table_name="state_vectors")
    op.drop_table("state_vectors")
