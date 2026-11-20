"""Add the Opportunity Engine table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20

Engine v1 Sprint 4 (Opportunity Engine): the opportunities table
— one discovered gap per row, linked to its entity, carrying its
detector, source record, confidence, and status.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "opportunity_type", sa.String(length=50), nullable=False
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="detected",
        ),
        sa.Column(
            "detector",
            sa.String(length=100),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("source_type", sa.String(length=100), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column(
            "evidence_id",
            sa.Integer(),
            sa.ForeignKey(
                "evidence_records.id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_opportunities_id", "opportunities", ["id"])
    op.create_index(
        "ix_opportunities_entity_id", "opportunities", ["entity_id"]
    )
    op.create_index(
        "ix_opportunities_opportunity_type",
        "opportunities",
        ["opportunity_type"],
    )
    op.create_index(
        "ix_opportunities_status", "opportunities", ["status"]
    )
    op.create_index(
        "ix_opportunities_detector", "opportunities", ["detector"]
    )
    op.create_index(
        "ix_opportunities_evidence_id",
        "opportunities",
        ["evidence_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opportunities_evidence_id", table_name="opportunities"
    )
    op.drop_index(
        "ix_opportunities_detector", table_name="opportunities"
    )
    op.drop_index(
        "ix_opportunities_status", table_name="opportunities"
    )
    op.drop_index(
        "ix_opportunities_opportunity_type",
        table_name="opportunities",
    )
    op.drop_index(
        "ix_opportunities_entity_id", table_name="opportunities"
    )
    op.drop_index("ix_opportunities_id", table_name="opportunities")
    op.drop_table("opportunities")
