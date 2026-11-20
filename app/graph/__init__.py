"""Knowledge Graph package — Engine v1 Sprint 3.

The abstraction layer: `IGraphRepository` (interface),
`PostgresGraphRepository` (the Engine v1 backend),
`Neo4jGraphRepository` (stub — not configured), and
`GraphService` (the business service, backend-agnostic).

`build_graph_service` is the per-request factory the API layer
uses: it wires the PostgreSQL repository into the service with
the request's session. Business code never constructs a backend
directly — it asks for a GraphService.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interface import IGraphRepository
from app.graph.neo4j import Neo4jGraphRepository
from app.graph.postgres import PostgresGraphRepository
from app.graph.service import GraphService

__all__ = [
    "GraphService",
    "IGraphRepository",
    "Neo4jGraphRepository",
    "PostgresGraphRepository",
    "build_graph_service",
]


def build_graph_service(db: AsyncSession) -> GraphService:
    """Wire the PostgreSQL repository into a GraphService for one
    request. This is the only place the API layer names a backend;
    everything downstream talks to the interface."""
    return GraphService(PostgresGraphRepository(db), db)
