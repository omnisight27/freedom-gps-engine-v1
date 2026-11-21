"""Add the Knowledge Graph tables and the Certification asset link.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20

Engine v1 Sprint 3 (Knowledge Graph Layer): the graph_nodes and
graph_edges tables backing the PostgreSQL graph repository, plus
the certifications.knowledge_asset_id column — the Sprint 3 fix
for the Sprint 2 title-matching limitation (a Certification now
links to its KnowledgeAsset representation, the same pattern
Skill has had since Sprint 2).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("node_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "node_type", "source_id", name="uq_graph_node_source"
        ),
    )
    op.create_index("ix_graph_nodes_id", "graph_nodes", ["id"])
    op.create_index(
        "ix_graph_nodes_node_type", "graph_nodes", ["node_type"]
    )
    op.create_index(
        "ix_graph_nodes_source_id", "graph_nodes", ["source_id"]
    )

    op.create_table(
        "graph_edges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "from_node_id",
            sa.Integer(),
            sa.ForeignKey("graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_node_id",
            sa.Integer(),
            sa.ForeignKey("graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relationship_type", sa.String(length=50), nullable=False
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "from_node_id",
            "to_node_id",
            "relationship_type",
            name="uq_graph_edge",
        ),
    )
    op.create_index("ix_graph_edges_id", "graph_edges", ["id"])
    op.create_index(
        "ix_graph_edges_from_node_id", "graph_edges", ["from_node_id"]
    )
    op.create_index(
        "ix_graph_edges_to_node_id", "graph_edges", ["to_node_id"]
    )
    op.create_index(
        "ix_graph_edges_relationship_type",
        "graph_edges",
        ["relationship_type"],
    )

    # Sprint 3 fix: Certification links to its KnowledgeAsset
    # representation (same pattern as Skill).
    op.add_column(
        "certifications",
        sa.Column(
            "knowledge_asset_id",
            sa.Integer(),
            sa.ForeignKey(
                "knowledge_assets.id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_certifications_knowledge_asset_id",
        "certifications",
        ["knowledge_asset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_certifications_knowledge_asset_id",
        table_name="certifications",
    )
    op.drop_column("certifications", "knowledge_asset_id")
    op.drop_index(
        "ix_graph_edges_relationship_type", table_name="graph_edges"
    )
    op.drop_index(
        "ix_graph_edges_to_node_id", table_name="graph_edges"
    )
    op.drop_index(
        "ix_graph_edges_from_node_id", table_name="graph_edges"
    )
    op.drop_index("ix_graph_edges_id", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index(
        "ix_graph_nodes_source_id", table_name="graph_nodes"
    )
    op.drop_index(
        "ix_graph_nodes_node_type", table_name="graph_nodes"
    )
    op.drop_index("ix_graph_nodes_id", table_name="graph_nodes")
    op.drop_table("graph_nodes")
