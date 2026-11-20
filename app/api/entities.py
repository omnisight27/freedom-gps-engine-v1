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

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.entity import Entity, EntityType
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
    """Create an entity. The caller becomes its owner."""
    entity = Entity(
        type=payload.type.value,
        name=payload.name,
        description=payload.description,
        owner_id=current_user.id,
    )
    db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return entity


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
    self-relationships and unknown target entities."""
    from_entity = await get_entity_or_404(db, entity_id)
    assert_entity_owner(from_entity, current_user)
    return await create_relationship(db, from_entity, payload)


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
