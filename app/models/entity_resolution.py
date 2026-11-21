"""EntityResolutionReview model — Phase C.1 (Entity Resolution).

The human review queue. An L4 context candidate (confidence
0.70-0.89) never auto-attaches: the resolver creates a new
entity for the incoming record and queues a row here proposing
the merge with the candidate. The operator approves (the two
entities merge, the proposed one folding into the matched one)
or rejects (the (proposed, matched) pair is suppressed from
future re-queuing).

The proposed entity is found at approve time by normalized_name:
the entity the L4 flow created carries canonical_name == the
proposed name, so its normalized key equals this row's
normalized_name, and no other entity can share that key (an L2
match would have attached to it instead). If the proposed
entity is gone by approve time, approval fails — the merge has
nothing to fold.
"""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"


class EntityResolutionReview(Base):
    __tablename__ = "entity_resolution_reviews"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # The incoming record's name as written (the spelling the L4
    # flow created an entity for).
    proposed_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # normalize_name(proposed_name) — the lookup key for the
    # proposed entity at approve time.
    normalized_name: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # The L4 candidate entity, or None when the review was queued
    # without a candidate (not used by the current engine — L4
    # always names one — but the column allows it).
    matched_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # The layer that produced the candidate ("L4").
    layer: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # The context that produced the candidate (domain, state,
    # level, shared token) — stored so the operator can judge it.
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=REVIEW_STATUS_PENDING
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    matched_entity = relationship("Entity")
