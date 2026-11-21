"""Entity endpoints — Engine v1 Sprint 1.

POST   /api/v1/entities                          create an entity
GET    /api/v1/entities                          list (filter by type)
GET    /api/v1/entities/{id}                     one entity, with relationships
PATCH  /api/v1/entities/{id}                     update name / description
POST   /api/v1/entities/{id}/relationships       create a relationship from it
GET    /api/v1/entities/{id}/relationships       list its relationships
GET    /api/v1/entities/{id}/neighbors           neighboring entities

All endpoints require auth. An entity's owner is its creator; owned
entities are visible only to their owner, while unowned entities are
shared. Writes require ownership.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.graph import build_graph_service
from app.models.entity import Entity, EntityType
from app.models.entity_resolution import (
    REVIEW_STATUS_PENDING,
    EntityResolutionReview,
)
from app.models.user import User
from app.schemas.entity import (
    EntityCreate,
    EntityDetailOut,
    EntityOut,
    EntityUpdate,
    NeighborOut,
    RelationshipCreate,
    RelationshipDetailOut,
    RelationshipOut,
    ReviewOut,
)
from app.services.entity_resolution import (
    approve_review,
    list_pending_reviews,
    reject_review,
    resolve_or_create,
)
from app.services.entity_service import (
    assert_entity_access,
    assert_entity_owner,
    create_relationship,
    entity_detail,
    get_entity_or_404,
    list_neighbors,
    list_relationships,
)

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])


@router.post(
    "", response_model=EntityOut, status_code=status.HTTP_201_CREATED
)
async def create_entity(
    payload: EntityCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entity:
    """Create an entity — through the Phase C.1 resolver.

    The caller becomes the owner of a NEW entity. When the name
    resolves to an entity the caller can already see (L1 exact,
    L2 normalized, L3 alias), the existing entity is returned
    (still 201: the endpoint's contract is "ensure this entity
    exists") with the incoming spelling recorded as an alias.
    An L4 context candidate never auto-attaches: a new entity is
    created and a review is queued instead.
    """
    result = await resolve_or_create(
        db,
        name=payload.name,
        entity_type=payload.type.value,
        owner_id=current_user.id,
        user=current_user,
    )
    entity = result.entity
    # Refresh the node's metadata (an attach may have added an
    # alias); the resolver already created the node for a new
    # entity, and sync is idempotent.
    service = build_graph_service(db)
    await service.sync_entity(entity)
    await db.commit()
    await db.refresh(entity)
    return entity


@router.get("/resolution-reviews", response_model=list[ReviewOut])
async def list_resolution_reviews_endpoint(
    status: str = Query(default=REVIEW_STATUS_PENDING),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list:
    """The human review queue (Phase C.1): L4 context
    candidates awaiting an operator's approve or reject. Any
    authenticated user may review — merges are audited with the
    actor, and the engine has no RBAC (see the deployment
    notes)."""
    return await list_pending_reviews(db, status=status)


@router.post(
    "/resolution-reviews/{review_id}/approve", response_model=ReviewOut
)
async def approve_resolution_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewOut:
    """Approve a pending review: merge the proposed entity into
    the matched (canonical) one. Returns the review, now
    approved."""
    try:
        await approve_review(db, review_id=review_id, user=current_user)
    except ValueError as exc:
        message = str(exc)
        if "not found" in message or "no longer exists" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=message
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=message
        ) from exc
    await db.commit()
    return await db.get(EntityResolutionReview, review_id)


@router.post(
    "/resolution-reviews/{review_id}/reject", response_model=ReviewOut
)
async def reject_resolution_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewOut:
    """Reject a pending review. The (proposed, matched) pair is
    suppressed from future re-queuing — a rejection is a human
    judgment that the two are different entities."""
    try:
        review = await reject_review(
            db, review_id=review_id, user=current_user
        )
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=message
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=message
        ) from exc
    await db.commit()
    return review


@router.get("", response_model=list[EntityOut])
async def list_entities(
    type: EntityType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Entity]:
    """List the caller's entities plus shared (unowned) entities,
    optionally filtered by type."""
    stmt = select(Entity).where(
        or_(Entity.owner_id == current_user.id, Entity.owner_id.is_(None))
    )
    if type is not None:
        stmt = stmt.where(Entity.type == type.value)
    stmt = stmt.order_by(Entity.created_at, Entity.id).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{entity_id}", response_model=EntityDetailOut)
async def get_entity(
    entity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntityDetailOut:
    """One entity, with every relationship it participates in."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_access(entity, current_user)
    return await entity_detail(db, entity)


@router.patch("/{entity_id}", response_model=EntityOut)
async def update_entity(
    entity_id: int,
    payload: EntityUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entity:
    """Update an entity's name and/or description. Owner only; the
    type never changes."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_owner(entity, current_user)
    if payload.name is not None:
        entity.name = payload.name
    if payload.description is not None:
        entity.description = payload.description
    await db.commit()
    await db.refresh(entity)
    # Sprint 3: refresh the node's metadata (name may have changed).
    service = build_graph_service(db)
    await service.sync_entity(entity)
    await db.commit()
    return entity


@router.post(
    "/{entity_id}/relationships",
    response_model=RelationshipOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_entity_relationship(
    entity_id: int,
    payload: RelationshipCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntityRelationship:
    """Create a directed relationship from this entity. Rejects
    self-relationships and unknown target entities. The graph edge
    between the two entity nodes is created automatically
    (Sprint 3)."""
    from_entity = await get_entity_or_404(db, entity_id)
    assert_entity_owner(from_entity, current_user)
    rel = await create_relationship(db, from_entity, payload)
    service = build_graph_service(db)
    await service.sync_entity_relationship(rel)
    await db.commit()
    return rel


@router.get(
    "/{entity_id}/relationships", response_model=list[RelationshipDetailOut]
)
async def list_entity_relationships(
    entity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RelationshipDetailOut]:
    """Every relationship the entity participates in, annotated with
    its direction relative to the entity."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_access(entity, current_user)
    return await list_relationships(db, entity)


@router.get("/{entity_id}/neighbors", response_model=list[NeighborOut])
async def list_entity_neighbors(
    entity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NeighborOut]:
    """Neighboring entities with the relationship that connects them."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_access(entity, current_user)
    return await list_neighbors(db, entity)
