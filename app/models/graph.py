"""Graph node and edge models — Engine v1 Sprint 3 (Knowledge Graph
Layer).

The graph is the system's relationships. Every major object —
Entity, KnowledgeAsset, Skill, Certification, EvidenceRecord,
FamilyRecord, LegacyAsset — becomes a GraphNode, and the links
between them become GraphEdges. The tables are the PostgreSQL
backing store for the graph abstraction layer (IGraphRepository);
Neo4j is a deployment option, not a dependency, so nothing here
requires a Neo4j server.

One node per source record: (node_type, source_id) is unique.
One edge per triple: (from_node_id, to_node_id, relationship_type)
is unique — the same relationship between the same two nodes is
recorded once.

`node_metadata` is a JSON object of denormalized source fields
(name, title, category, …) so a node is useful without joining
back to its source table. The source record is always the truth;
the metadata is a convenience copy, refreshed by the sync
functions in the graph service whenever the record changes.

Edges carry lineage directly: confidence (0–1), source, and
created_at are on every edge, so every relationship traces to
evidence (AT-307).
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GraphNodeType(str, enum.Enum):
    """The object types that become graph nodes."""

    ENTITY = "Entity"
    KNOWLEDGE_ASSET = "KnowledgeAsset"
    SKILL = "Skill"
    CERTIFICATION = "Certification"
    EVIDENCE_RECORD = "EvidenceRecord"
    FAMILY_RECORD = "FamilyRecord"
    LEGACY_ASSET = "LegacyAsset"


class GraphRelationshipType(str, enum.Enum):
    """The relationship types Sprint 3 records.

    OWNS is the generic ownership edge (an entity owns its
    records). HAS_SKILL, HAS_CERTIFICATION, HAS_RECORD, and
    HAS_LEGACY are the typed edges for the registries.
    HAS_EVIDENCE links an asset — or a skill / certification
    through its linked asset — to its evidence. RELATED_TO,
    MEMBER_OF, and CONTRIBUTES_TO carry the Sprint 1
    entity-to-entity relationships into the graph (works_with and
    inherits both map to RELATED_TO).
    """

    OWNS = "OWNS"
    HAS_SKILL = "HAS_SKILL"
    HAS_CERTIFICATION = "HAS_CERTIFICATION"
    HAS_EVIDENCE = "HAS_EVIDENCE"
    HAS_RECORD = "HAS_RECORD"
    HAS_LEGACY = "HAS_LEGACY"
    RELATED_TO = "RELATED_TO"
    MEMBER_OF = "MEMBER_OF"
    CONTRIBUTES_TO = "CONTRIBUTES_TO"


class GraphNode(Base):
    """One graph node per source record.

    `node_type` names the source table (GraphNodeType) and
    `source_id` is the source record's id. Together they are
    unique: a record has exactly one node.
    """

    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint(
            "node_type", "source_id", name="uq_graph_node_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    node_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Denormalized source fields (JSON) — a convenience copy; the
    # source record is the truth and sync refreshes this.
    node_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    outgoing_edges = relationship(
        "GraphEdge",
        foreign_keys="GraphEdge.from_node_id",
        back_populates="from_node",
        cascade="all, delete-orphan",
    )
    incoming_edges = relationship(
        "GraphEdge",
        foreign_keys="GraphEdge.to_node_id",
        back_populates="to_node",
        cascade="all, delete-orphan",
    )


class GraphEdge(Base):
    """One directed graph edge between two nodes.

    Lineage lives on the edge itself: confidence, source, and
    created_at (AT-307). Deleting a node deletes its edges
    (CASCADE at the database level).
    """

    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "from_node_id",
            "to_node_id",
            "relationship_type",
            name="uq_graph_edge",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    from_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    to_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0
    )
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    from_node = relationship(
        "GraphNode",
        foreign_keys=[from_node_id],
        back_populates="outgoing_edges",
    )
    to_node = relationship(
        "GraphNode",
        foreign_keys=[to_node_id],
        back_populates="incoming_edges",
    )
