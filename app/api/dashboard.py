"""Sovereign Dashboard endpoints — Engine v1 Sprint 6.

GET    /api/v1/dashboard                 Executive Overview (Module 1)
GET    /api/v1/dashboard/ledger          Complete Ledger (Module 2)
GET    /api/v1/dashboard/state-vector   State Vector Explorer (Module 3)
GET    /api/v1/dashboard/opportunities  Opportunity Center (Module 4)
GET    /api/v1/dashboard/vault          Knowledge Vault (Module 5)
GET    /api/v1/dashboard/validation     Validation Center (Module 6)
GET    /api/v1/dashboard/audit          Audit history (AT-608)
GET    /dashboard                        the dashboard page (HTML)

All endpoints require auth and are entity-scoped (the Sprint 1
read rule: own entities plus shared ones). Every endpoint
assembles its module from the dashboard service, which reads
only through the existing services — no direct database access,
no new scoring, no new models.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dashboard_page import render_dashboard_page
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.dashboard import (
    AuditOut,
    ExecutiveOverviewOut,
    LedgerOut,
    OpportunityCenterOut,
    StateVectorExplorerOut,
    ValidationCenterOut,
    VaultOut,
)
from app.services.dashboard_service import (
    get_audit_history,
    get_executive_overview,
    get_ledger,
    get_opportunity_center,
    get_state_vector_explorer,
    get_validation_center,
    get_vault_view,
)
from app.services.vault_service import (
    accessible_entity_ids,
    resolve_entity_for_read,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

page_router = APIRouter(tags=["dashboard"])


async def _resolve(
    db: AsyncSession, entity_id: int, user: User
):
    """Load an entity for a dashboard read (404 when unknown,
    403 unless the caller may access it)."""
    return await resolve_entity_for_read(db, entity_id, user)


@router.get("", response_model=ExecutiveOverviewOut)
async def executive_overview(
    entity_id: int = Query(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExecutiveOverviewOut:
    """Module 1: one-screen summary (AT-601)."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_executive_overview(db, current_user, entity)


@router.get("/ledger", response_model=LedgerOut)
async def ledger(
    entity_id: int = Query(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LedgerOut:
    """Module 2: the five capitals with trend indicators."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_ledger(db, current_user, entity)


@router.get("/state-vector", response_model=StateVectorExplorerOut)
async def state_vector_explorer(
    entity_id: int = Query(),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StateVectorExplorerOut:
    """Module 3: Ω(t) current value, latest changes, history
    (AT-602)."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_state_vector_explorer(
        db, current_user, entity, limit=limit
    )


@router.get("/opportunities", response_model=OpportunityCenterOut)
async def opportunity_center(
    entity_id: int = Query(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OpportunityCenterOut:
    """Module 4: open / dismissed / resolved opportunities,
    each with source, confidence, evidence, and reason (AT-603)."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_opportunity_center(db, current_user, entity)


@router.get("/vault", response_model=VaultOut)
async def vault_view(
    entity_id: int = Query(),
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VaultOut:
    """Module 5: the Knowledge Vault — searchable (?q=) and
    filterable (?category=) (AT-604, AT-607)."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_vault_view(
        db, current_user, entity, q=q, category=category
    )


@router.get("/validation", response_model=ValidationCenterOut)
async def validation_center(
    entity_id: int = Query(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ValidationCenterOut:
    """Module 6: models, validation levels, predictions,
    outcomes, calibration errors, drift records (AT-605)."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_validation_center(db, current_user, entity)


@router.get("/audit", response_model=AuditOut)
async def audit_history(
    entity_id: int = Query(),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuditOut:
    """An entity's audit history, newest first (AT-608)."""
    entity = await _resolve(db, entity_id, current_user)
    return await get_audit_history(
        db, current_user, entity, limit=limit
    )


@page_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """The Sovereign Dashboard page (AT-601): one HTML page,
    six sections, rendered from the same dashboard service as
    the JSON endpoints. With no entity_id, the caller's first
    accessible entity is shown."""
    if entity_id is None:
        ids = await accessible_entity_ids(db, current_user)
        if not ids:
            return HTMLResponse(
                "<!DOCTYPE html><html><head><title>Freedom GP$ — "
                "Sovereign Dashboard</title></head><body>"
                "<h1>Freedom GP$ — Sovereign Dashboard</h1>"
                "<p>No entities yet. Create an entity to see "
                "your dashboard.</p></body></html>"
            )
        entity_id = sorted(ids)[0]
    entity = await resolve_entity_for_read(db, entity_id, current_user)
    overview = await get_executive_overview(db, current_user, entity)
    ledger = await get_ledger(db, current_user, entity)
    explorer = await get_state_vector_explorer(
        db, current_user, entity
    )
    opportunities = await get_opportunity_center(
        db, current_user, entity
    )
    vault = await get_vault_view(db, current_user, entity)
    validation = await get_validation_center(db, current_user, entity)
    return HTMLResponse(
        render_dashboard_page(
            overview, ledger, explorer, opportunities, vault,
            validation,
        )
    )
