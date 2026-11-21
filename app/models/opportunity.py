"""Opportunity model — Engine v1 Sprint 4 (Opportunity Engine).

Sprint Goal: implement the Opportunity Engine — the first discovery
capability. Mission: What is missing? What should be connected?
What creates value next? The system now has Identity, State
Vector, Memory, and Relationships; the next logical capability is
Discovery, and this is where Freedom GP$ first starts producing
value that users can actually see.

An Opportunity is one discovered gap: a skill with no evidence, a
certification with evidence but no connections, a knowledge asset
with no relationships, a legacy asset with no preservation status,
or a disconnected cluster in the graph. The opportunity itself is
the output — there is no separate Recommendation model in Sprint 4.

Lineage: every opportunity belongs to an entity and carries its
detector (which detector found it), the source record it refers to
(source_type + source_id), and timestamps. Creation and status
changes write AuditLog entries (AT-407). Detectors are idempotent:
re-running them never duplicates an open opportunity for the same
detector + source record, and a gap that closes dismisses its open
opportunity automatically (see opportunity_service).

Confidence is the detector's certainty in the finding (0–1), not a
value estimate. The detector confidences are illustrative:
skills_no_evidence 0.9, skills_evidence_no_utilization 0.7,
certifications_evidence_no_utilization 0.7,
knowledge_assets_no_relationships 0.85,
legacy_no_preservation_status 0.9, disconnected_clusters 0.8.

Out of scope for Sprint 4 (do not add): AI Reasoning,
Forecasting, Simulation, Trust Engine v2, new scores.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OpportunityType(str, enum.Enum):
    """The kinds of opportunity the Engine detects."""

    SKILL = "Skill Opportunity"
    ASSET = "Asset Opportunity"
    KNOWLEDGE = "Knowledge Opportunity"
    RELATIONSHIP = "Relationship Opportunity"
    LEGACY = "Legacy Opportunity"


class OpportunityStatus(str, enum.Enum):
    """The lifecycle of an opportunity."""

    DETECTED = "detected"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    REALIZED = "realized"


class Opportunity(Base):
    """One discovered opportunity — a gap in the ledger.

    `detector` names the detector that found it (or "manual" for a
    hand-created opportunity). `source_type` / `source_id` name the
    record the opportunity refers to (a Skill, KnowledgeAsset,
    LegacyAsset, Certification, or "GraphCluster" for a
    disconnected cluster, which has no single source record and
    carries source_id None). `evidence_id` links supporting
    evidence when the opportunity itself is evidenced.
    """

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    opportunity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="detected", index=True
    )
    # Lineage: which detector found it, and which record it
    # refers to.
    detector: Mapped[str] = mapped_column(
        String(100), nullable=False, default="manual", index=True
    )
    source_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    source_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_records.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    entity = relationship("Entity", back_populates="opportunities")
    evidence = relationship("EvidenceRecord")
