"""Knowledge Graph endpoints — Engine v1 Sprint 3.

GET    /api/v1/graph/nodes                       list accessible nodes
GET    /api/v1/graph/nodes/{id}                  one node
GET    /api/v1/graph/entities/{id}/graph         an entity's subgraph
GET    /api/v1/graph/nodes/{id}/traverse         breadth-first walk
GET    /api/v1/graph/path                        shortest path, two nodes
POST   /api/v1/graph/relationships              create a relationship
GET    /api/v1/graph/relationships              list relationships
DELETE /api/v1/graph/relationships/{id}          delete a relationship
POST   /api/v1/graph/sync                        backfill nodes + edges
GET    /api/v1/graph/queries/skills-no-evidence
GET    /api/v1/graph/queries/certifications-no-evidence
GET    /api/v1/graph/queries/knowledge-no-evidence
GET    /api/v1/graph/queries/legacy-no-preservation-status

All endpoints require auth. A node is readable when its source
record's entity is readable; writing a relationship requires
owning the from-node's entity (the Sprint 1 ownership rule,
unchanged). Nodes are created automatically when their source
record is created — there is no POST /graph/nodes in Sprint 3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.graph import build_graph_service
from app.graph.service import GraphService
from app.models.graph import GraphNodeType
from app.models.user import User
from app.models.vault import Certification, KnowledgeAsset, LegacyAsset, Skill
from app.schemas.graph import (
    GraphEdgeOut,
    GraphNodeOut,
    GraphPathOut,
    GraphRelationshipCreate,
    GraphSubgraphOut,
    GraphSyncOut,
    GraphTraverseOut,
)
from app.schemas.vault import (
    CertificationOut,
    KnowledgeAssetOut,
    LegacyAssetOut,
    SkillOut,
)
from app.services.entity_service import (
    assert_entity_access,
    get_entity_or_404,
)

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


def get_graph_service(
    db: AsyncSession = Depends(get_db),
) -> GraphService:
    """Per-request GraphService (PostgreSQL backend)."""
    return build_graph_service(db)


# --- Nodes ---

@router.get("/nodes", response_model=list[GraphNodeOut])
async def list_nodes(
    node_type: GraphNodeType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
):
    """Nodes the caller may read (their entities plus shared
    ones), optionally filtered by type."""
    return await service.list_accessible_nodes(
        current_user,
        node_type=node_type.value if node_type is not None else None,
        limit=limit,
        offset=offset,
    )


@router.get("/nodes/{node_id}", response_model=GraphNodeOut)
async def get_node(
    node_id: int,
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
):
    """One graph node (AT-301: the node is retrievable)."""
    return await service.get_node_for_read(node_id, current_user)


@router.get(
    "/entities/{entity_id}/graph", response_model=GraphSubgraphOut
)
async def get_entity_graph(
    entity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: GraphService = Depends(get_graph_service),
):
    """An entity's subgraph: its node, its 1-hop neighbors, and
    the edges between them."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_access(entity, current_user)
    center, nodes, edges = await service.subgraph_for_entity(entity)
    return GraphSubgraphOut(
        entity_id=entity.id,
        center_node=center,
        nodes=nodes,
        edges=edges,
    )


@router.get(
    "/nodes/{node_id}/traverse", response_model=GraphTraverseOut
)
async def traverse_node(
    node_id: int,
    depth: int = Query(default=2, ge=0, le=5),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
):
    """Breadth-first walk from one node out to `depth` hops."""
    start = await service.get_node_for_read(node_id, current_user)
    nodes, edges = await service.traverse(node_id, depth=depth)
    return GraphTraverseOut(
        start_node=start, depth=depth, nodes=nodes, edges=edges
    )


@router.get("/path", response_model=GraphPathOut)
async def find_path(
    from_node_id: int = Query(),
    to_node_id: int = Query(),
    max_depth: int = Query(default=5, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
):
    """Shortest path between two nodes (AT-305). Both endpoints
    must be readable by the caller."""
    await service.get_node_for_read(from_node_id, current_user)
    await service.get_node_for_read(to_node_id, current_user)
    nodes, edges = await service.find_path(
        from_node_id, to_node_id, max_depth=max_depth
    )
    return GraphPathOut(
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        found=bool(nodes),
        nodes=nodes,
        edges=edges,
    )


# --- Relationships ---

@router.post(
    "/relationships",
    response_model=GraphEdgeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_relationship(
    payload: GraphRelationshipCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: GraphService = Depends(get_graph_service),
):
    """Create a relationship between two nodes by hand (AT-302).
    Rejects unknown nodes (404), a self-edge (400), and a
    duplicate (409). Writes an audit entry (AT-306)."""
    edge = await service.create_relationship(
        user=current_user,
        from_node_id=payload.from_node_id,
        to_node_id=payload.to_node_id,
        relationship_type=payload.relationship_type.value,
        confidence=payload.confidence,
        source=payload.source,
    )
    await db.commit()
    await db.refresh(edge)
    return edge


@router.get("/relationships", response_model=list[GraphEdgeOut])
async def list_relationships(
    from_node_id: int | None = Query(default=None),
    to_node_id: int | None = Query(default=None),
    relationship_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
):
    """Relationships visible to the caller: edges whose from-node
    belongs to an entity they may read."""
    return await service.list_accessible_edges(
        current_user,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        relationship_type=relationship_type,
        limit=limit,
        offset=offset,
    )


@router.delete(
    "/relationships/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_relationship(
    edge_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: GraphService = Depends(get_graph_service),
) -> None:
    """Delete a relationship. Writes an audit entry."""
    await service.delete_relationship(user=current_user, edge_id=edge_id)
    await db.commit()


# --- Sync / backfill ---

@router.post("/sync", response_model=GraphSyncOut)
async def sync_graph(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: GraphService = Depends(get_graph_service),
):
    """Backfill: create nodes and edges for every record on every
    entity the caller may read. Idempotent — a second run creates
    nothing."""
    result = await service.sync_all(current_user)
    await db.commit()
    return GraphSyncOut(**result)


# --- Knowledge inventory queries (graph-based, AT-304) ---

@router.get(
    "/queries/skills-no-evidence", response_model=list[SkillOut]
)
async def get_skills_no_evidence(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
) -> list[Skill]:
    """Skills with no evidence — decided by the graph (no
    outgoing HAS_EVIDENCE edge)."""
    return await service.query_skills_no_evidence(
        current_user, entity_id
    )


@router.get(
    "/queries/certifications-no-evidence",
    response_model=list[CertificationOut],
)
async def get_certifications_no_evidence(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
) -> list[Certification]:
    """Certifications with no evidence — decided by the graph,
    through the real asset link (the Sprint 3 fix)."""
    return await service.query_certifications_no_evidence(
        current_user, entity_id
    )


@router.get(
    "/queries/knowledge-no-evidence",
    response_model=list[KnowledgeAssetOut],
)
async def get_knowledge_no_evidence(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
) -> list[KnowledgeAsset]:
    """Knowledge assets with no supporting evidence — decided by
    the graph (AT-304)."""
    return await service.query_knowledge_no_evidence(
        current_user, entity_id
    )


@router.get(
    "/queries/legacy-no-preservation-status",
    response_model=list[LegacyAssetOut],
)
async def get_legacy_no_preservation_status(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: GraphService = Depends(get_graph_service),
) -> list[LegacyAsset]:
    """Legacy assets whose preservation status was never set."""
    return await service.query_legacy_no_preservation_status(
        current_user, entity_id
    )
