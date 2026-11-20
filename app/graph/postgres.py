"""PostgresGraphRepository — the PostgreSQL-backed implementation
of IGraphRepository (Sprint 3).

The real graph store for Engine v1: GraphNode and GraphEdge rows
in PostgreSQL (SQLite in tests, where the same SQLAlchemy models
run). Methods add to the session and flush; they do not commit —
the caller owns the transaction.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import GraphEdge, GraphNode


class PostgresGraphRepository:
    """IGraphRepository backed by the graph_nodes / graph_edges
    tables."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_node(
        self,
        *,
        node_type: str,
        source_id: int,
        metadata: dict | None = None,
    ) -> GraphNode:
        node = GraphNode(
            node_type=node_type,
            source_id=source_id,
            node_metadata=metadata,
        )
        self.db.add(node)
        await self.db.flush()
        return node

    async def get_node(self, node_id: int) -> GraphNode | None:
        result = await self.db.execute(
            select(GraphNode).where(GraphNode.id == node_id)
        )
        return result.scalar_one_or_none()

    async def get_node_by_source(
        self, node_type: str, source_id: int
    ) -> GraphNode | None:
        result = await self.db.execute(
            select(GraphNode).where(
                GraphNode.node_type == node_type,
                GraphNode.source_id == source_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_nodes(
        self,
        *,
        node_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphNode]:
        stmt = select(GraphNode).order_by(
            GraphNode.created_at, GraphNode.id
        )
        if node_type is not None:
            stmt = stmt.where(GraphNode.node_type == node_type)
        stmt = stmt.limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_node_metadata(
        self, node: GraphNode, metadata: dict | None
    ) -> GraphNode:
        node.node_metadata = metadata
        await self.db.flush()
        return node

    async def delete_node(self, node_id: int) -> bool:
        node = await self.get_node(node_id)
        if node is None:
            return False
        await self.db.delete(node)
        await self.db.flush()
        return True

    async def create_edge(
        self,
        *,
        from_node_id: int,
        to_node_id: int,
        relationship_type: str,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> GraphEdge:
        edge = GraphEdge(
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            relationship_type=relationship_type,
            confidence=confidence,
            source=source,
        )
        self.db.add(edge)
        await self.db.flush()
        return edge

    async def get_edge(self, edge_id: int) -> GraphEdge | None:
        result = await self.db.execute(
            select(GraphEdge).where(GraphEdge.id == edge_id)
        )
        return result.scalar_one_or_none()

    async def get_edge_by_triple(
        self, from_node_id: int, to_node_id: int, relationship_type: str
    ) -> GraphEdge | None:
        result = await self.db.execute(
            select(GraphEdge).where(
                GraphEdge.from_node_id == from_node_id,
                GraphEdge.to_node_id == to_node_id,
                GraphEdge.relationship_type == relationship_type,
            )
        )
        return result.scalar_one_or_none()

    async def list_edges(
        self,
        *,
        from_node_id: int | None = None,
        to_node_id: int | None = None,
        relationship_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphEdge]:
        stmt = select(GraphEdge).order_by(
            GraphEdge.created_at, GraphEdge.id
        )
        if from_node_id is not None:
            stmt = stmt.where(GraphEdge.from_node_id == from_node_id)
        if to_node_id is not None:
            stmt = stmt.where(GraphEdge.to_node_id == to_node_id)
        if relationship_type is not None:
            stmt = stmt.where(
                GraphEdge.relationship_type == relationship_type
            )
        stmt = stmt.limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

    async def delete_edge(self, edge_id: int) -> bool:
        edge = await self.get_edge(edge_id)
        if edge is None:
            return False
        await self.db.delete(edge)
        await self.db.flush()
        return True

    async def neighbors(
        self, node_id: int, *, relationship_type: str | None = None
    ) -> list[tuple[GraphEdge, GraphNode]]:
        """(edge, neighbor) pairs, both directions: edges where the
        node is the from-end (neighbor is the to-end) and edges
        where it is the to-end (neighbor is the from-end)."""
        stmt = (
            select(GraphEdge)
            .where(
                or_(
                    GraphEdge.from_node_id == node_id,
                    GraphEdge.to_node_id == node_id,
                )
            )
            .order_by(GraphEdge.created_at, GraphEdge.id)
        )
        if relationship_type is not None:
            stmt = stmt.where(
                GraphEdge.relationship_type == relationship_type
            )
        edges = list((await self.db.execute(stmt)).scalars().all())
        pairs: list[tuple[GraphEdge, GraphNode]] = []
        for edge in edges:
            other_id = (
                edge.to_node_id
                if edge.from_node_id == node_id
                else edge.from_node_id
            )
            neighbor = await self.get_node(other_id)
            if neighbor is not None:
                pairs.append((edge, neighbor))
        return pairs
