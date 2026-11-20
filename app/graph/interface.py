"""IGraphRepository — the graph abstraction interface (Sprint 3).

The business services depend ONLY on this interface (AT-308).
PostgresGraphRepository is the real implementation, backed by the
graph_nodes / graph_edges tables. Neo4jGraphRepository is a stub
with the same interface — it raises NotImplementedError until a
Neo4j deployment exists. Nothing in the service layer knows which
backend is behind the interface.

The interface is storage primitives only: nodes, edges, and
neighbors. Traversal, path finding, sync, and the inventory
queries live in the GraphService, composed from these primitives
so they work against any backend.

Repository methods add to the session and flush; they do not
commit. The caller (service or API layer) owns the transaction.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.graph import GraphEdge, GraphNode


@runtime_checkable
class IGraphRepository(Protocol):
    """The graph storage contract every backend must honor."""

    async def create_node(
        self,
        *,
        node_type: str,
        source_id: int,
        metadata: dict | None = None,
    ) -> GraphNode:
        """Insert one node. The caller guarantees (node_type,
        source_id) is not already a node."""
        ...

    async def get_node(self, node_id: int) -> GraphNode | None:
        """One node by its graph id, or None."""
        ...

    async def get_node_by_source(
        self, node_type: str, source_id: int
    ) -> GraphNode | None:
        """The node for one source record, or None."""
        ...

    async def list_nodes(
        self,
        *,
        node_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphNode]:
        """Nodes, optionally filtered by type, oldest first."""
        ...

    async def update_node_metadata(
        self, node: GraphNode, metadata: dict | None
    ) -> GraphNode:
        """Replace a node's metadata (sync refresh)."""
        ...

    async def delete_node(self, node_id: int) -> bool:
        """Delete a node (its edges go with it). True when a node
        existed and was deleted."""
        ...

    async def create_edge(
        self,
        *,
        from_node_id: int,
        to_node_id: int,
        relationship_type: str,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> GraphEdge:
        """Insert one edge. The caller guarantees the triple
        (from, to, type) does not already exist."""
        ...

    async def get_edge(self, edge_id: int) -> GraphEdge | None:
        """One edge by id, or None."""
        ...

    async def get_edge_by_triple(
        self, from_node_id: int, to_node_id: int, relationship_type: str
    ) -> GraphEdge | None:
        """The edge for one (from, to, type) triple, or None."""
        ...

    async def list_edges(
        self,
        *,
        from_node_id: int | None = None,
        to_node_id: int | None = None,
        relationship_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphEdge]:
        """Edges, optionally filtered, oldest first."""
        ...

    async def delete_edge(self, edge_id: int) -> bool:
        """Delete one edge. True when an edge existed and was
        deleted."""
        ...

    async def neighbors(
        self, node_id: int, *, relationship_type: str | None = None
    ) -> list[tuple[GraphEdge, GraphNode]]:
        """(edge, neighbor) pairs for one node, both directions.
        Each pair's edge connects the node to the neighbor; the
        neighbor is the node on the other end."""
        ...
