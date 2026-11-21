"""Neo4jGraphRepository — the Neo4j-backed implementation of
IGraphRepository (Sprint 3: STUB).

The class exists with the same interface so the abstraction is
real: swapping backends means swapping this class in, not
rewriting the services. Every method raises NotImplementedError
until a Neo4j deployment exists for Engine v1.

Deliberately, this module imports NO neo4j driver and requires NO
Neo4j server. Engine v1 runs on PostgreSQL; Neo4j is a deployment
optimization for later, per Engineering Order 001 (Validation >
Performance).
"""

from __future__ import annotations

from app.models.graph import GraphEdge, GraphNode

_NOT_CONFIGURED = (
    "Neo4j is not configured for Engine v1: the graph runs on "
    "PostgreSQL (PostgresGraphRepository). Deploy Neo4j and "
    "implement this repository before using it."
)


class Neo4jGraphRepository:
    """IGraphRepository backed by Neo4j — not implemented in
    Engine v1."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def create_node(
        self,
        *,
        node_type: str,
        source_id: int,
        metadata: dict | None = None,
    ) -> GraphNode:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def get_node(self, node_id: int) -> GraphNode | None:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def get_node_by_source(
        self, node_type: str, source_id: int
    ) -> GraphNode | None:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def list_nodes(
        self,
        *,
        node_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphNode]:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def update_node_metadata(
        self, node: GraphNode, metadata: dict | None
    ) -> GraphNode:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def delete_node(self, node_id: int) -> bool:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def create_edge(
        self,
        *,
        from_node_id: int,
        to_node_id: int,
        relationship_type: str,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> GraphEdge:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def get_edge(self, edge_id: int) -> GraphEdge | None:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def get_edge_by_triple(
        self, from_node_id: int, to_node_id: int, relationship_type: str
    ) -> GraphEdge | None:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def list_edges(
        self,
        *,
        from_node_id: int | None = None,
        to_node_id: int | None = None,
        relationship_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphEdge]:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def delete_edge(self, edge_id: int) -> bool:
        raise NotImplementedError(_NOT_CONFIGURED)

    async def neighbors(
        self, node_id: int, *, relationship_type: str | None = None
    ) -> list[tuple[GraphEdge, GraphNode]]:
        raise NotImplementedError(_NOT_CONFIGURED)
