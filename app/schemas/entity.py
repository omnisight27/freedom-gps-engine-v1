"""Pydantic v2 request/response models for entities and relationships.

Entity types: Person, Family, Business, Nonprofit, Community.
Relationship types: owns, member_of, works_with, inherits,
contributes_to. Types are validated here as enums; the models store
them as plain strings, matching the repo's style for type columns.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.entity import EntityType, RelationshipType


# --- Entity ---
class EntityCreate(BaseModel):
    """Create an entity. The owner is always the authenticated caller."""

    type: EntityType
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class EntityUpdate(BaseModel):
    """Update an entity's name and/or description. Type never changes."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    name: str
    owner_id: int | None
    description: str | None
    # Phase C.1 — Entity Resolution: the canonical display
    # form, the known alias spellings, and the layer confidence
    # that produced the canonical record.
    canonical_name: str
    aliases: list[str]
    confidence_score: float
    created_at: datetime
    updated_at: datetime


# --- Resolution reviews (Phase C.1) ---
class ReviewOut(BaseModel):
    """One row of the human review queue (L4 candidates)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    proposed_name: str
    normalized_name: str
    entity_type: str
    matched_entity_id: int | None
    layer: str
    confidence: float
    context: dict | None
    status: str
    created_at: datetime
    resolved_at: datetime | None


# --- Relationship ---
class RelationshipCreate(BaseModel):
    """Create a directed relationship from one entity to another.

    A self-relationship (from == to) is rejected at the API layer.
    """

    to_entity_id: int
    relationship_type: RelationshipType
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: str | None = Field(default=None, max_length=200)


class RelationshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_entity_id: int
    to_entity_id: int
    relationship_type: str
    confidence: float
    source: str | None
    created_at: datetime


class RelationshipDetailOut(RelationshipOut):
    """A relationship annotated with its direction relative to the
    entity it was listed from."""

    direction: str  # "outgoing" (entity is the source) or "incoming"


class EntityDetailOut(EntityOut):
    """An entity with every relationship it participates in."""

    relationships: list[RelationshipDetailOut] = []


class NeighborOut(BaseModel):
    """One neighboring entity, with the relationship that connects it."""

    entity: EntityOut
    relationship_type: str
    direction: str  # "outgoing" or "incoming"
