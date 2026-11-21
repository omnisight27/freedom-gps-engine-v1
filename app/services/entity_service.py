"""Entity service — lookup, ownership, relationships, neighbors.

Ownership rule (Sprint 1): an entity with an owner may only be read or
changed by that owner. An entity with no owner (owner_id is None) is
shared — any authenticated user may read it and record relationships
from it. Creation always sets the caller as owner.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity import Entity, EntityRelationship
from app.models.user import User
from app.schemas.entity import (
    EntityDetailOut,
    NeighborOut,
    RelationshipCreate,
    RelationshipDetailOut,
    RelationshipOut,
)


async def get_entity_or_404(db: AsyncSession, entity_id: int) -> Entity:
    """Load an entity by id or raise 404."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity not found",
        )
    return entity


def assert_entity_access(entity: Entity, current_user: User) -> None:
    """Raise 403 when an owned entity belongs to someone else."""
    if entity.owner_id is not None and entity.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only access your own entities",
        )


def assert_entity_owner(entity: Entity, current_user: User) -> None:
    """Raise 403 unless the caller owns the entity. Used for writes."""
    if entity.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only change your own entities",
        )


async def list_relationships(
    db: AsyncSession, entity: Entity
) -> list[RelationshipDetailOut]:
    """Every relationship the entity participates in, annotated with
    its direction relative to the entity."""
    result = await db.execute(
        select(EntityRelationship)
        .where(
            or_(
                EntityRelationship.from_entity_id == entity.id,
                EntityRelationship.to_entity_id == entity.id,
            )
        )
        .order_by(EntityRelationship.created_at, EntityRelationship.id)
    )
    rows = result.scalars().all()
    return [
        RelationshipDetailOut(
            **RelationshipOut.model_validate(row).model_dump(),
            direction=(
                "outgoing"
                if row.from_entity_id == entity.id
                else "incoming"
            ),
        )
        for row in rows
    ]


async def entity_detail(
    db: AsyncSession, entity: Entity
) -> EntityDetailOut:
    """An entity with every relationship it participates in."""
    return EntityDetailOut(
        **entity.__dict__,
        relationships=await list_relationships(db, entity),
    )


async def create_relationship(
    db: AsyncSession,
    from_entity: Entity,
    payload: RelationshipCreate,
) -> EntityRelationship:
    """Create a directed relationship from `from_entity`.

    Rejects self-relationships (from == to) and unknown target
    entities. The caller must already own (or share) `from_entity`.
    """
    if payload.to_entity_id == from_entity.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An entity cannot have a relationship with itself",
        )
    to_entity = await get_entity_or_404(db, payload.to_entity_id)
    rel = EntityRelationship(
        from_entity_id=from_entity.id,
        to_entity_id=to_entity.id,
        relationship_type=payload.relationship_type.value,
        confidence=payload.confidence,
        source=payload.source,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return rel


async def list_neighbors(
    db: AsyncSession, entity: Entity
) -> list[NeighborOut]:
    """Neighboring entities with the relationship that connects them.

    One row per (neighbor, relationship type, direction) — an entity
    connected by two different relationship types appears twice, once
    per edge.
    """
    from app.schemas.entity import EntityOut

    result = await db.execute(
        select(EntityRelationship)
        .where(
            or_(
                EntityRelationship.from_entity_id == entity.id,
                EntityRelationship.to_entity_id == entity.id,
            )
        )
        .order_by(EntityRelationship.created_at, EntityRelationship.id)
    )
    rows = result.scalars().all()
    neighbors: list[NeighborOut] = []
    seen: set[tuple[int, str, str]] = set()
    for row in rows:
        if row.from_entity_id == entity.id:
            neighbor_id, direction = row.to_entity_id, "outgoing"
        else:
            neighbor_id, direction = row.from_entity_id, "incoming"
        key = (neighbor_id, row.relationship_type, direction)
        if key in seen:
            continue
        seen.add(key)
        neighbor = await get_entity_or_404(db, neighbor_id)
        neighbors.append(
            NeighborOut(
                entity=EntityOut.model_validate(neighbor),
                relationship_type=row.relationship_type,
                direction=direction,
            )
        )
    return neighbors
