"""Pydantic v2 request/response models for the Knowledge Vault.

Engine v1 Sprint 2 (formal definition). Enums are validated here;
the models store them as plain strings, matching the repo's style
for type columns. Estimated impact is an abstract-unit ranking aid,
never dollars and never a valuation.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.vault import (
    EvidenceSourceType,
    FamilyRecordType,
    KnowledgeCategory,
    LegacyAssetType,
)


# --- KnowledgeAsset (the unified vault record) ---
class KnowledgeAssetCreate(BaseModel):
    entity_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    category: KnowledgeCategory
    content_type: str | None = Field(default=None, max_length=100)
    status: str = Field(default="active", max_length=50)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str | None = Field(default=None, max_length=500)


class KnowledgeAssetUpdate(BaseModel):
    """PUT body: only supplied fields change; version increments
    on every successful update."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    category: KnowledgeCategory | None = None
    content_type: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=50)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str | None = Field(default=None, max_length=500)


class KnowledgeAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    title: str
    description: str | None
    category: str
    content_type: str | None
    status: str
    confidence: float | None
    created_by: int | None
    source: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class KnowledgeLineageOut(BaseModel):
    """The lineage view (Epic 2.2, Story 2.2.2): who created the
    asset, where it came from, which version it is, when it was
    last updated, and how much evidence supports it. `last_updated`
    is the asset's updated_at, exposed under the lineage name."""

    knowledge_asset_id: int
    created_by: int | None
    source: str | None
    version: int
    last_updated: datetime
    evidence_count: int


# --- Evidence ---
class EvidenceCreate(BaseModel):
    """Attach one piece of evidence to a KnowledgeAsset."""

    source: str = Field(min_length=1, max_length=500)
    source_type: EvidenceSourceType
    confidence: float = Field(ge=0, le=1)
    collected_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_asset_id: int
    source: str
    source_type: str
    confidence: float
    collected_at: datetime
    notes: str | None
    created_at: datetime
    updated_at: datetime


# --- Skills ---
class SkillCreate(BaseModel):
    entity_id: int
    name: str = Field(min_length=1, max_length=200)
    proficiency: str | None = Field(default=None, max_length=50)
    years_experience: float | None = Field(default=None, ge=0)
    verified: bool = False
    knowledge_asset_id: int | None = None


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    proficiency: str | None = Field(default=None, max_length=50)
    years_experience: float | None = Field(default=None, ge=0)
    verified: bool | None = None
    knowledge_asset_id: int | None = None


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    knowledge_asset_id: int | None
    name: str
    proficiency: str | None
    years_experience: float | None
    verified: bool
    created_at: datetime
    updated_at: datetime


# --- Certifications ---
class CertificationCreate(BaseModel):
    entity_id: int
    issuer: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    issue_date: date | None = None
    expiration_date: date | None = None
    credential_id: str | None = Field(default=None, max_length=200)


class CertificationUpdate(BaseModel):
    issuer: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    issue_date: date | None = None
    expiration_date: date | None = None
    credential_id: str | None = Field(default=None, max_length=200)


class CertificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    issuer: str | None
    title: str
    issue_date: date | None
    expiration_date: date | None
    credential_id: str | None
    created_at: datetime
    updated_at: datetime


# --- Family records ---
class FamilyRecordCreate(BaseModel):
    entity_id: int
    title: str = Field(min_length=1, max_length=200)
    record_type: FamilyRecordType
    description: str | None = Field(default=None, max_length=20000)
    date_recorded: date | None = None


class FamilyRecordUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    record_type: FamilyRecordType | None = None
    description: str | None = Field(default=None, max_length=20000)
    date_recorded: date | None = None


class FamilyRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    title: str
    record_type: str
    description: str | None
    date_recorded: date | None
    created_at: datetime
    updated_at: datetime


# --- Legacy assets ---
class LegacyAssetCreate(BaseModel):
    entity_id: int
    title: str = Field(min_length=1, max_length=200)
    asset_type: LegacyAssetType
    estimated_impact: float | None = Field(default=None, ge=0)
    preservation_status: str | None = Field(default=None, max_length=100)


class LegacyAssetUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    asset_type: LegacyAssetType | None = None
    estimated_impact: float | None = Field(default=None, ge=0)
    preservation_status: str | None = Field(default=None, max_length=100)


class LegacyAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    title: str
    asset_type: str
    estimated_impact: float | None
    preservation_status: str | None
    created_at: datetime
    updated_at: datetime


# --- Audit log ---
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str | None
    entity_id: int | None
    record_type: str | None
    record_id: int | None
    actor: str | None
    timestamp: datetime
    details: str | None


# --- Knowledge inventory (AT-006) ---
class SkillInventory(BaseModel):
    count: int
    items: list[SkillOut]


class CertificationInventory(BaseModel):
    count: int
    items: list[CertificationOut]


class KnowledgeAssetInventory(BaseModel):
    count: int
    items: list[KnowledgeAssetOut]


class KnowledgeInventoryOut(BaseModel):
    entity_id: int
    skills: SkillInventory
    certifications: CertificationInventory
    knowledge_assets: KnowledgeAssetInventory
