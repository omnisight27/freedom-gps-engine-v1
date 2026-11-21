"""Pydantic v2 request/response models for the Knowledge Graph.

Engine v1 Sprint 3. Enums are validated here; the models store
them as plain strings, matching the repo's style for type columns.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.graph import GraphNodeType, GraphRelationshipType


# --- Nodes ---
class GraphNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_type: str
    source_id: int
    node_metadata: dict | None
    created_at: datetime


# --- Edges ---
class GraphRelationshipCreate(BaseModel):
    """Create a relationship between two graph nodes by hand."""

    from_node_id: int
    to_node_id: int
    relationship_type: GraphRelationshipType
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: str | None = Field(default=None, max_length=200)


class GraphEdgeOut(BaseModel):
    """One graph edge — lineage is on the edge itself: confidence,
    source, and created_at (AT-307)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    from_node_id: int
    to_node_id: int
    relationship_type: str
    confidence: float
    source: str | None
    created_at: datetime


# --- Subgraph / traversal / path ---
class GraphSubgraphOut(BaseModel):
    """An entity's subgraph: its node, its 1-hop neighbors, and
    the edges between them."""

    entity_id: int
    center_node: GraphNodeOut
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class GraphTraverseOut(BaseModel):
    """A breadth-first walk from one node out to `depth` hops."""

    start_node: GraphNodeOut
    depth: int
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class GraphPathOut(BaseModel):
    """Shortest path between two nodes. `found` is false (and the
    lists empty) when no path exists within max_depth."""

    from_node_id: int
    to_node_id: int
    found: bool
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


# --- Sync ---
class GraphSyncOut(BaseModel):
    """The backfill result: what the sync created, and the totals
    after. Idempotent — a second run creates nothing."""

    nodes_created: int
    edges_created: int
    nodes_total: int
    edges_total: int
