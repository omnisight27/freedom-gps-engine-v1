"""Entity and EntityRelationship models — Engine v1 Sprint 1.

An Entity is any actor the ledger measures: a Person, Family, Business,
Nonprofit, or Community. Entities are the nodes of the Knowledge Graph
and the owners of state vectors; Identity is the root node of all value
measurement.

An EntityRelationship is one directed edge between two entities:
owns, member_of, works_with, inherits, contributes_to. Edges carry a
confidence score and a source so every relationship traces to evidence.
"""

from __future__ import annotations

import enum

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EntityType(str, enum.Enum):
    """The five entity types Engine v1 measures."""

    PERSON = "person"
    FAMILY = "family"
    BUSINESS = "business"
    NONPROFIT = "nonprofit"
    COMMUNITY = "community"


class RelationshipType(str, enum.Enum):
    """The five relationship types Engine v1 records."""

    OWNS = "owns"
    MEMBER_OF = "member_of"
    WORKS_WITH = "works_with"
    INHERITS = "inherits"
    CONTRIBUTES_TO = "contributes_to"


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Stored as a plain string (validated against EntityType at the API
    # layer), matching the repo's style for type columns.
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner = relationship("User", back_populates="entities")
    outgoing_relationships = relationship(
        "EntityRelationship",
        foreign_keys="EntityRelationship.from_entity_id",
        back_populates="from_entity",
        cascade="all, delete-orphan",
    )
    incoming_relationships = relationship(
        "EntityRelationship",
        foreign_keys="EntityRelationship.to_entity_id",
        back_populates="to_entity",
        cascade="all, delete-orphan",
    )
    state_vectors = relationship("StateVector", back_populates="entity")


class EntityRelationship(Base):
    __tablename__ = "entity_relationships"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    from_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    to_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Stored as a plain string (validated against RelationshipType at
    # the API layer).
    relationship_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0
    )
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    from_entity = relationship(
        "Entity",
        foreign_keys=[from_entity_id],
        back_populates="outgoing_relationships",
    )
    to_entity = relationship(
        "Entity",
        foreign_keys=[to_entity_id],
        back_populates="incoming_relationships",
    )
