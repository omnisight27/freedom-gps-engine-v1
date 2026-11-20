"""Sovereign Dashboard service — Engine v1 Sprint 6.

The dashboard is the surface. It assembles the six modules from
the existing services — State Vector, Knowledge Vault,
Opportunity Engine, Validation Engine — and nothing else. NO
direct database access: every number here is read through a
service (state_vector_service, vault_service,
opportunity_service, registry_service). No new scoring, no new
models, no new tables. Trend indicators are comparisons of
state vector history (latest vs previous snapshot), not scores.

Architecture (Engineering Order 006):
User -> Sovereign Dashboard -> OmniSight Assistant -> Engine
Services -> Knowledge Graph -> Validation Layer.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity import Entity
from app.models.opportunity import OpportunityStatus
from app.models.user import User
from app.models.vault import EvidenceRecord, KnowledgeAsset
from app.schemas.dashboard import (
    AuditOut,
    CapitalTrend,
    DashboardOpportunityOut,
    ExecutiveOverviewOut,
    LedgerOut,
    OpportunityCenterOut,
    StateVectorChange,
    StateVectorExplorerOut,
    ValidationCenterOut,
    VaultOut,
)
from app.schemas.opportunity import OpportunityOut
from app.schemas.vault import EvidenceOut
from app.services.opportunity_service import list_opportunities
from app.services.registry_service import (
    list_calibration,
    list_drift,
    list_models,
    list_outcomes,
    list_predictions,
    list_scores,
)
from app.services.state_vector_service import (
    get_entity_state_vector_history,
    get_latest_entity_state_vector,
)
from app.services.vault_service import (
    assert_record_access,
    get_record_or_404,
    list_audit_log_for_entity,
    list_certifications_for_entity,
    list_evidence_for_entity,
    list_family_records_for_entity,
    list_knowledge_assets,
    list_legacy_assets_for_entity,
    list_skills_for_entity,
)

# Ω(t) = [F,H,S,R,L,T,M,I,E,O] — the ten components, in order,
# with their plain-English names.
COMPONENTS: list[tuple[str, str, str]] = [
    ("financial", "F", "Financial"),
    ("human", "H", "Human"),
    ("social", "S", "Social"),
    ("resource", "R", "Resource"),
    ("legacy", "L", "Legacy"),
    ("trust", "T", "Trust"),
    ("memory", "M", "Memory"),
    ("intelligence", "I", "Intelligence"),
    ("entropy", "E", "Entropy"),
    ("opportunity", "O", "Opportunity"),
]

# The five capitals of the Complete Ledger.
CAPITALS: list[tuple[str, str, str]] = COMPONENTS[:5]


def _trend(
    current: float | None, previous: float | None
) -> tuple[float | None, str]:
    """Compare two measurements. Unmeasured (None) on either
    side means no trend can be computed — flat, with no delta."""
    if current is None or previous is None:
        return None, "flat"
    delta = current - previous
    if delta > 0:
        return delta, "up"
    if delta < 0:
        return delta, "down"
    return 0.0, "flat"


async def _enrich_opportunity(
    db: AsyncSession, user: User, opp
) -> DashboardOpportunityOut:
    """One opportunity with its evidence attached, so the
    dashboard shows source, confidence, evidence, and reason.
    Everything is explainable."""
    evidence = None
    if opp.evidence_id is not None:
        record = await get_record_or_404(
            db, EvidenceRecord, opp.evidence_id
        )
        # Evidence follows the Sprint 1 read rule through its
        # KnowledgeAsset's entity — EvidenceRecord itself has no
        # entity_id.
        asset = await get_record_or_404(
            db, KnowledgeAsset, record.knowledge_asset_id
        )
        await assert_record_access(db, asset, user)
        evidence = EvidenceOut.model_validate(record)
    return DashboardOpportunityOut(
        **OpportunityOut.model_validate(opp).model_dump(),
        evidence=evidence,
    )


async def get_executive_overview(
    db: AsyncSession, user: User, entity: Entity
) -> ExecutiveOverviewOut:
    """Module 1: one-screen summary."""
    latest = await get_latest_entity_state_vector(db, entity.id)
    all_opps = await list_opportunities(
        db, user, entity_id=entity.id, limit=200
    )
    open_count = sum(
        1
        for opp in all_opps
        if opp.status
        in (
            OpportunityStatus.DETECTED.value,
            OpportunityStatus.ACCEPTED.value,
        )
    )
    assets = await list_knowledge_assets(
        db, user, entity.id, limit=200
    )
    legacy = await list_legacy_assets_for_entity(
        db, user, entity.id, limit=200
    )
    skills = await list_skills_for_entity(db, user, entity.id, limit=200)
    certs = await list_certifications_for_entity(
        db, user, entity.id, limit=200
    )
    models = await list_models(db, limit=200)
    validation_status: dict[str, int] = {}
    for model in models:
        validation_status[model.validation_level] = (
            validation_status.get(model.validation_level, 0) + 1
        )
    return ExecutiveOverviewOut(
        entity=entity,
        state_vector=latest,
        opportunity_count=open_count,
        knowledge_assets_count=len(assets),
        legacy_assets_count=len(legacy),
        skills_count=len(skills),
        certifications_count=len(certs),
        validation_status=validation_status,
    )


async def get_ledger(
    db: AsyncSession, user: User, entity: Entity
) -> LedgerOut:
    """Module 2: the five capitals, read from Ω(t), each with a
    trend indicator from state vector history. Trend indicators
    only — no new scoring."""
    history = await get_entity_state_vector_history(db, entity.id, limit=2)
    latest = history[0] if history else None
    previous = history[1] if len(history) > 1 else None
    capitals: list[CapitalTrend] = []
    for key, letter, name in CAPITALS:
        value = getattr(latest, key) if latest is not None else None
        prev_value = (
            getattr(previous, key) if previous is not None else None
        )
        delta, trend = _trend(value, prev_value)
        capitals.append(
            CapitalTrend(
                key=letter,
                name=name,
                value=value,
                previous_value=prev_value,
                delta=delta,
                trend=trend,
            )
        )
    return LedgerOut(
        entity_id=entity.id,
        capitals=capitals,
        as_of=latest.recorded_at if latest is not None else None,
    )


async def get_state_vector_explorer(
    db: AsyncSession,
    user: User,
    entity: Entity,
    limit: int = 50,
) -> StateVectorExplorerOut:
    """Module 3: Ω(t) current value, latest changes, history."""
    history = await get_entity_state_vector_history(
        db, entity.id, limit=limit
    )
    latest = history[0] if history else None
    previous = history[1] if len(history) > 1 else None
    changes: list[StateVectorChange] = []
    if latest is not None and previous is not None:
        for key, letter, name in COMPONENTS:
            current = getattr(latest, key)
            prev = getattr(previous, key)
            if current is None or prev is None:
                continue
            if current == prev:
                continue
            changes.append(
                StateVectorChange(
                    component=letter,
                    name=name,
                    previous=prev,
                    current=current,
                    delta=current - prev,
                )
            )
    return StateVectorExplorerOut(
        entity_id=entity.id,
        current=latest,
        changes=changes,
        history=history,
    )


async def get_opportunity_center(
    db: AsyncSession, user: User, entity: Entity
) -> OpportunityCenterOut:
    """Module 4: open / dismissed / resolved opportunities,
    each with source, confidence, evidence, and reason."""
    all_opps = await list_opportunities(
        db, user, entity_id=entity.id, limit=200
    )
    open_list: list[DashboardOpportunityOut] = []
    dismissed_list: list[DashboardOpportunityOut] = []
    resolved_list: list[DashboardOpportunityOut] = []
    for opp in all_opps:
        enriched = await _enrich_opportunity(db, user, opp)
        if opp.status in (
            OpportunityStatus.DETECTED.value,
            OpportunityStatus.ACCEPTED.value,
        ):
            open_list.append(enriched)
        elif opp.status == OpportunityStatus.DISMISSED.value:
            dismissed_list.append(enriched)
        elif opp.status == OpportunityStatus.REALIZED.value:
            resolved_list.append(enriched)
    return OpportunityCenterOut(
        entity_id=entity.id,
        open=open_list,
        dismissed=dismissed_list,
        resolved=resolved_list,
        open_count=len(open_list),
        dismissed_count=len(dismissed_list),
        resolved_count=len(resolved_list),
    )


async def get_vault_view(
    db: AsyncSession,
    user: User,
    entity: Entity,
    *,
    q: str | None = None,
    category: str | None = None,
) -> VaultOut:
    """Module 5: the Knowledge Vault — assets, skills,
    certifications, family records, legacy assets, evidence.
    Searchable (?q=) and filterable (?category=)."""
    assets = await list_knowledge_assets(
        db, user, entity.id, q=q, category=category, limit=200
    )
    skills = await list_skills_for_entity(db, user, entity.id, limit=200)
    certs = await list_certifications_for_entity(
        db, user, entity.id, limit=200
    )
    family = await list_family_records_for_entity(
        db, user, entity.id, limit=200
    )
    legacy = await list_legacy_assets_for_entity(
        db, user, entity.id, limit=200
    )
    evidence = await list_evidence_for_entity(
        db, user, entity.id, limit=200
    )
    return VaultOut(
        entity_id=entity.id,
        knowledge_assets=assets,
        skills=skills,
        certifications=certs,
        family_records=family,
        legacy_assets=legacy,
        evidence=evidence,
        knowledge_assets_count=len(assets),
        skills_count=len(skills),
        certifications_count=len(certs),
        family_records_count=len(family),
        legacy_assets_count=len(legacy),
        evidence_count=len(evidence),
    )


async def get_validation_center(
    db: AsyncSession, user: User, entity: Entity
) -> ValidationCenterOut:
    """Module 6: the science layer, exposed — models,
    validation levels, predictions, outcomes, calibration
    errors, drift records. Most apps hide uncertainty;
    Freedom GP$ exposes it."""
    models = await list_models(db, limit=200)
    scores = await list_scores(db, limit=200)
    validation_levels: dict[str, int] = {}
    for model in models:
        validation_levels[model.validation_level] = (
            validation_levels.get(model.validation_level, 0) + 1
        )
    predictions = await list_predictions(
        db, user, entity_id=entity.id, limit=200
    )
    outcomes = await list_outcomes(db, user, limit=200)
    calibration = await list_calibration(db, limit=200)
    drift = await list_drift(db, limit=200)
    return ValidationCenterOut(
        entity_id=entity.id,
        models=models,
        scores=scores,
        validation_levels=validation_levels,
        predictions=predictions,
        outcomes=outcomes,
        calibration_records=calibration,
        drift_records=drift,
    )


async def get_audit_history(
    db: AsyncSession, user: User, entity: Entity, limit: int = 50
) -> AuditOut:
    """An entity's audit history, newest first (AT-608)."""
    entries = await list_audit_log_for_entity(
        db, user, entity.id, limit=limit
    )
    return AuditOut(entity_id=entity.id, entries=entries)
