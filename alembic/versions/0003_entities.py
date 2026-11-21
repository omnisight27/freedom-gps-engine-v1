"""Add entities, entity_relationships, and StateVector.entity_id.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20

Engine v1 Sprint 1: the Entity model (Person, Family, Business,
Nonprofit, Community) and the EntityRelationship model (owns, member_of,
works_with, inherits, contributes_to), plus an optional entity_id on
state_vectors so a snapshot can belong to an entity's time series.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=True),
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
    op.create_index("ix_entities_id", "entities", ["id"])
    op.create_index("ix_entities_type", "entities", ["type"])
    op.create_index("ix_entities_owner_id", "entities", ["owner_id"])

    op.create_table(
        "entity_relationships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "from_entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_entity_relationships_id", "entity_relationships", ["id"]
    )
    op.create_index(
        "ix_entity_relationships_from_entity_id",
        "entity_relationships",
        ["from_entity_id"],
    )
    op.create_index(
        "ix_entity_relationships_to_entity_id",
        "entity_relationships",
        ["to_entity_id"],
    )
    op.create_index(
        "ix_entity_relationships_relationship_type",
        "entity_relationships",
        ["relationship_type"],
    )

    op.add_column(
        "state_vectors",
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_state_vectors_entity_id", "state_vectors", ["entity_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_state_vectors_entity_id", table_name="state_vectors")
    op.drop_column("state_vectors", "entity_id")
    op.drop_index(
        "ix_entity_relationships_relationship_type",
        table_name="entity_relationships",
    )
    op.drop_index(
        "ix_entity_relationships_to_entity_id",
        table_name="entity_relationships",
    )
    op.drop_index(
        "ix_entity_relationships_from_entity_id",
        table_name="entity_relationships",
    )
    op.drop_index(
        "ix_entity_relationships_id", table_name="entity_relationships"
    )
    op.drop_table("entity_relationships")
    op.drop_index("ix_entities_owner_id", table_name="entities")
    op.drop_index("ix_entities_type", table_name="entities")
    op.drop_index("ix_entities_id", table_name="entities")
    op.drop_table("entities")
