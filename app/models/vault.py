"""Knowledge Vault models — Engine v1 Sprint 2 (formal definition).

Sprint Goal: implement the Freedom GP$ Knowledge Vault capable of
storing, organizing, linking, and retrieving knowledge, memory,
legacy, and evidence records associated with any entity.

The Vault is the system's memory. KnowledgeAsset is the unified
record: skills, certifications, documents, research, processes,
lessons, historical records, family records, and legacy assets are
all KnowledgeAssets, each with its own category. Skill,
Certification, FamilyRecord, and LegacyAsset are the per-registry
records that sit alongside the unified asset; a Skill may link to
its KnowledgeAsset representation so evidence attaches through the
asset.

Lineage (Epic 2.2, Story 2.2.2): every KnowledgeAsset supports
created_by (the creating user), source, version (starts at 1,
increments on update), last_updated, and evidence_count (computed
count of linked EvidenceRecords). The lineage view is exposed at
GET /knowledge/{id}/lineage. `last_updated` is the asset's
updated_at, exposed under the lineage name — one timestamp, two
names, no duplicate column.

Estimated impact on LegacyAsset is measured in abstract units, not
dollars and not a valuation — a relative ranking aid, never a price.

Out of scope for Sprint 2 (do not add): Opportunity Repository
(deferred to Sprint 4), a separate MemoryRecord model (memory is
covered by KnowledgeAsset categories), Neo4j integration, AI agents,
Governance, Forecasting, new scores.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    Boolean,
    Date,
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


class KnowledgeCategory(str, enum.Enum):
    """The categories a KnowledgeAsset may hold."""

    SKILL = "Skill"
    CERTIFICATION = "Certification"
    DOCUMENT = "Document"
    RESEARCH = "Research"
    PROCESS = "Process"
    LESSON = "Lesson"
    HISTORICAL_RECORD = "Historical Record"
    FAMILY_RECORD = "Family Record"
    LEGACY_ASSET = "Legacy Asset"


class EvidenceSourceType(str, enum.Enum):
    """Where a piece of evidence came from."""

    DOCUMENT = "Document"
    TRANSCRIPT = "Transcript"
    CERTIFICATE = "Certificate"
    USER_ASSERTION = "User Assertion"
    GOVERNMENT_RECORD = "Government Record"
    ARCHIVE_RECORD = "Archive Record"
    MANUAL_ENTRY = "Manual Entry"


class FamilyRecordType(str, enum.Enum):
    """The kinds of family record the Vault keeps."""

    BIOGRAPHY = "Biography"
    GENEALOGY = "Genealogy"
    HISTORICAL_EVENT = "Historical Event"
    ORAL_HISTORY = "Oral History"
    ARTIFACT = "Artifact"
    LETTER = "Letter"


class LegacyAssetType(str, enum.Enum):
    """The kinds of legacy asset the Vault preserves."""

    BOOK = "Book"
    PATENT = "Patent"
    RESEARCH = "Research"
    ARCHIVE = "Archive"
    TRAINING = "Training"
    COMMUNITY_PROJECT = "Community Project"
    IP = "IP"


class KnowledgeAsset(Base):
    """The unified Knowledge Vault record.

    One row per knowledge asset, whatever its category. Every asset
    belongs to an entity, carries lineage (created_by, source,
    version, last_updated), and may hold many EvidenceRecords.
    """

    __tablename__ = "knowledge_assets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    content_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active"
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Lineage (Epic 2.2): who created it, where it came from, which
    # version it is. Version starts at 1 and increments on update.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    source: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
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

    entity = relationship("Entity", back_populates="knowledge_assets")
    creator = relationship("User")
    evidence_records = relationship(
        "EvidenceRecord",
        back_populates="asset",
        cascade="all, delete-orphan",
    )


class EvidenceRecord(Base):
    """One piece of evidence attached to a KnowledgeAsset.

    Evidence is what makes a record trustworthy: before a score, a
    skill, a legacy asset, or an opportunity can be trusted, the
    evidence behind it must exist and be checkable. Many evidence
    records may support one asset.
    """

    __tablename__ = "evidence_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    knowledge_asset_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    collected_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    asset = relationship("KnowledgeAsset", back_populates="evidence_records")


class Skill(Base):
    """One skill held by an entity.

    A skill may have many evidence records: it does so through its
    optional KnowledgeAsset representation — set knowledge_asset_id
    to the asset (category "Skill") and attach evidence to the
    asset. A skill with no linked asset has no evidence.
    `verified` records whether the skill has been verified; it
    defaults to false.
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    knowledge_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_assets.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    proficiency: Mapped[str | None] = mapped_column(String(50), nullable=True)
    years_experience: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
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

    entity = relationship("Entity", back_populates="skills")
    asset = relationship("KnowledgeAsset")


class Certification(Base):
    """One certification held by an entity.

    Sprint 3 fix: a certification now carries an optional
    knowledge_asset_id FK to its KnowledgeAsset representation
    (category "Certification"), the same pattern Skill has had
    since Sprint 2. Evidence attaches through the asset, so the
    certifications-no-evidence query uses a real link instead of
    matching by title. Certifications created before the link
    existed keep working: the Vault query also honors the title
    match as a fallback (see vault_service)."""

    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    knowledge_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_assets.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[object | None] = mapped_column(
        Date, nullable=True
    )
    credential_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True
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

    entity = relationship("Entity", back_populates="certifications")
    asset = relationship("KnowledgeAsset")


class FamilyRecord(Base):
    """One family record — a biography, genealogy, historical event,
    oral history, artifact, or letter.

    Family records preserve the history that no balance sheet ever
    captures. Creation is written to the audit log (Epic 2.8).
    """

    __tablename__ = "family_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    record_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_recorded: Mapped[object | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    entity = relationship("Entity", back_populates="family_records")


class LegacyAsset(Base):
    """One preserved legacy asset — a book, patent, research work,
    archive, training, community project, or IP.

    Legacy is prosperity preserved across generations: value that
    survives because someone recorded it. `preservation_status`
    tracks whether that preservation is in place; updating it
    writes an audit event (AT-004).
    """

    __tablename__ = "legacy_assets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    estimated_impact: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    preservation_status: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
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

    entity = relationship("Entity", back_populates="legacy_assets")


class AuditLog(Base):
    """One audit event (Epic 2.8).

    Every significant creation writes an entry: who did it, when,
    to which entity and record. Named creation events:
    knowledge_created, skill_added, certification_added,
    legacy_created. FamilyRecord creation is also written here, but
    no named event type was invented for it — the entry carries
    event_type NULL and the record_type / record_id / actor /
    timestamp / details carry the record.

    `event_type` is nullable for exactly that reason; every other
    entry carries one of the named events (or
    legacy_preservation_updated, written when a LegacyAsset's
    preservation_status changes).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    record_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timestamp: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    entity = relationship("Entity")
