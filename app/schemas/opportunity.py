"""Pydantic v2 request/response models for the Opportunity Engine.

Engine v1 Sprint 4. Enums are validated here; the models store
them as plain strings, matching the repo's style for type columns.
Confidence is the detector's certainty in the finding (0–1), not a
value estimate and never dollars.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.opportunity import OpportunityStatus, OpportunityType


# --- Opportunity ---
class OpportunityCreate(BaseModel):
    entity_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    opportunity_type: OpportunityType
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: OpportunityStatus = OpportunityStatus.DETECTED
    detector: str = Field(default="manual", max_length=100)
    source_type: str | None = Field(default=None, max_length=100)
    source_id: int | None = None
    evidence_id: int | None = None


class OpportunityUpdate(BaseModel):
    """PATCH body: only supplied, non-None values change (None
    means "no change", matching the repo's PATCH semantics)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    opportunity_type: OpportunityType | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: OpportunityStatus | None = None


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    title: str
    description: str | None
    opportunity_type: str
    confidence: float
    status: str
    detector: str
    source_type: str | None
    source_id: int | None
    evidence_id: int | None
    created_at: datetime
    updated_at: datetime


# --- Detection run ---
class DetectRequest(BaseModel):
    entity_id: int


class DetectResponse(BaseModel):
    """What one detection run did: the opportunities it created
    and the open ones whose gap had closed and were auto-dismissed.
    """

    entity_id: int
    created_count: int
    dismissed_count: int
    created: list[OpportunityOut]
    dismissed: list[OpportunityOut]
