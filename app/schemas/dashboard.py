"""Pydantic v2 response models for the Sovereign Dashboard.

Engine v1 Sprint 6 (Engineering Order 006). The dashboard is the
surface: six modules assembled from the existing services —
State Vector, Knowledge Vault, Opportunity Engine, Validation
Engine. No new scoring, no new models, no new tables; every
number here already exists in a service, the dashboard only
displays it. Trend indicators are computed from state vector
history (latest vs previous snapshot): up / down / flat.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.entity import EntityOut
from app.schemas.opportunity import OpportunityOut
from app.schemas.registry import (
    CalibrationOut,
    DriftOut,
    ModelOut,
    OutcomeOut,
    PredictionOut,
    ScoreOut,
)
from app.schemas.state_vector import StateVectorOut
from app.schemas.vault import (
    AuditLogOut,
    CertificationOut,
    EvidenceOut,
    FamilyRecordOut,
    KnowledgeAssetOut,
    LegacyAssetOut,
    SkillOut,
)


# --- Module 1: Executive Overview ---
class ExecutiveOverviewOut(BaseModel):
    """One-screen summary: the entity, its latest state vector,
    and the counts that matter."""

    entity: EntityOut
    state_vector: StateVectorOut | None
    opportunity_count: int = Field(
        description="Open opportunities (detected + accepted)."
    )
    knowledge_assets_count: int
    legacy_assets_count: int
    skills_count: int
    certifications_count: int
    validation_status: dict[str, int] = Field(
        description="Models by validation level."
    )


# --- Module 2: Complete Ledger ---
class CapitalTrend(BaseModel):
    """One capital of the ledger with its trend indicator,
    computed from state vector history (latest vs previous
    snapshot). No new scoring — the value is read, the trend is
    a comparison."""

    key: str = Field(description="F, H, S, R, or L.")
    name: str
    value: float | None
    previous_value: float | None = Field(
        description="None when fewer than two snapshots exist."
    )
    delta: float | None = Field(
        description="current - previous; None when unmeasured."
    )
    trend: str = Field(description="up, down, or flat.")


class LedgerOut(BaseModel):
    """The five capitals — Financial, Human, Social, Resource,
    Legacy — read from Ω(t), each with a trend indicator."""

    entity_id: int
    capitals: list[CapitalTrend]
    as_of: datetime | None


# --- Module 3: State Vector Explorer ---
class StateVectorChange(BaseModel):
    """One component that moved between the latest two
    snapshots."""

    component: str = Field(description="One of F H S R L T M I E O.")
    name: str
    previous: float | None
    current: float | None
    delta: float | None


class StateVectorExplorerOut(BaseModel):
    """Ω(t) = [F,H,S,R,L,T,M,I,E,O]: current value, latest
    changes, and history."""

    entity_id: int
    current: StateVectorOut | None
    changes: list[StateVectorChange] = Field(
        description="Components that moved since the previous snapshot."
    )
    history: list[StateVectorOut]


# --- Module 4: Opportunity Center ---
class DashboardOpportunityOut(OpportunityOut):
    """One opportunity as the dashboard shows it: source
    (detector + source record), confidence, evidence, and
    reason (description). Everything is explainable."""

    evidence: EvidenceOut | None = Field(
        default=None,
        description="The evidence record linked to this opportunity, if any.",
    )


class OpportunityCenterOut(BaseModel):
    """Open = detected + accepted; Dismissed = dismissed;
    Resolved = realized."""

    entity_id: int
    open: list[DashboardOpportunityOut]
    dismissed: list[DashboardOpportunityOut]
    resolved: list[DashboardOpportunityOut]
    open_count: int
    dismissed_count: int
    resolved_count: int


# --- Module 5: Knowledge Vault ---
class VaultOut(BaseModel):
    """The Vault as the dashboard shows it: knowledge assets,
    skills, certifications, family records, legacy assets,
    evidence. Searchable (?q=) and filterable (?category=).
    Every record carries its lineage (created_by, source,
    version) on the asset itself."""

    entity_id: int
    knowledge_assets: list[KnowledgeAssetOut]
    skills: list[SkillOut]
    certifications: list[CertificationOut]
    family_records: list[FamilyRecordOut]
    legacy_assets: list[LegacyAssetOut]
    evidence: list[EvidenceOut]
    knowledge_assets_count: int
    skills_count: int
    certifications_count: int
    family_records_count: int
    legacy_assets_count: int
    evidence_count: int


# --- Module 6: Validation Center ---
class ValidationCenterOut(BaseModel):
    """The science layer, exposed: models, validation levels,
    predictions, outcomes, calibration errors, drift records.
    Most apps hide uncertainty; Freedom GP$ exposes it."""

    entity_id: int
    models: list[ModelOut]
    scores: list[ScoreOut]
    validation_levels: dict[str, int] = Field(
        description="Models by validation level."
    )
    predictions: list[PredictionOut]
    outcomes: list[OutcomeOut]
    calibration_records: list[CalibrationOut]
    drift_records: list[DriftOut]


# --- Audit history ---
class AuditOut(BaseModel):
    """An entity's audit history, newest first."""

    entity_id: int
    entries: list[AuditLogOut]
