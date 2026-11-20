"""Opportunity Engine endpoints — Engine v1 Sprint 4.

POST   /api/v1/opportunities                 create an opportunity
GET    /api/v1/opportunities                 list (filters: entity_id,
                                             status, opportunity_type)
GET    /api/v1/opportunities/{id}            one opportunity
PATCH  /api/v1/opportunities/{id}            update (status changes
                                             write an audit entry)
DELETE /api/v1/opportunities/{id}            delete an opportunity
GET    /api/v1/entities/{id}/opportunities   an entity's opportunities
POST   /api/v1/opportunities/detect          run all six detectors
                                             for one entity

All endpoints require auth. Reads follow the Sprint 1 rule (own
entities plus shared ones); writes — creating, updating, deleting,
and running detection — require owning the entity.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.graph import build_graph_service
from app.graph.service import GraphService
from app.models.opportunity import Opportunity, OpportunityStatus
from app.models.user import User
from app.schemas.opportunity import (
    DetectRequest,
    DetectResponse,
    OpportunityCreate,
    OpportunityOut,
    OpportunityUpdate,
)
from app.services.entity_service import (
    assert_entity_owner,
    get_entity_or_404,
)
from app.services.opportunity_service import (
    assert_opportunity_access,
    assert_opportunity_owner,
    create_opportunity,
    delete_opportunity,
    get_opportunity_or_404,
    list_opportunities,
    run_detection,
    update_opportunity,
)

router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])

entity_router = APIRouter(prefix="/api/v1/entities", tags=["entities"])


def get_graph_service(
    db: AsyncSession = Depends(get_db),
) -> GraphService:
    """Per-request GraphService (PostgreSQL backend) for the
    detectors."""
    return build_graph_service(db)


@router.post(
    "", response_model=OpportunityOut, status_code=status.HTTP_201_CREATED
)
async def create(
    payload: OpportunityCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Opportunity:
    """Create an opportunity by hand (AT-404). Writes an audit
    entry (AT-407)."""
    opp = await create_opportunity(db, current_user, payload)
    await db.commit()
    await db.refresh(opp)
    return opp


@router.get("", response_model=list[OpportunityOut])
async def list_all(
    entity_id: int | None = Query(default=None),
    status_filter: OpportunityStatus | None = Query(
        default=None, alias="status"
    ),
    opportunity_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Opportunity]:
    """Opportunities on entities the caller may read, optionally
    filtered by entity, status, or type."""
    return await list_opportunities(
        db,
        current_user,
        entity_id=entity_id,
        status_filter=(
            status_filter.value if status_filter is not None else None
        ),
        opportunity_type=opportunity_type,
        limit=limit,
        offset=offset,
    )


@router.post("/detect", response_model=DetectResponse)
async def detect(
    payload: DetectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    graph: GraphService = Depends(get_graph_service),
) -> DetectResponse:
    """Run all six detectors for one entity. Idempotent: a second
    run creates no duplicates, and gaps that closed dismiss their
    open opportunities automatically."""
    entity = await get_entity_or_404(db, payload.entity_id)
    assert_entity_owner(entity, current_user)
    result = await run_detection(db, graph, current_user, entity)
    await db.commit()
    # Refresh: the UPDATEs expire the server-set updated_at, and
    # the response must be serialized from loaded attributes.
    for opp in result["created"] + result["dismissed"]:
        await db.refresh(opp)
    return DetectResponse(
        entity_id=entity.id,
        created_count=len(result["created"]),
        dismissed_count=len(result["dismissed"]),
        created=result["created"],
        dismissed=result["dismissed"],
    )


@router.get("/{opportunity_id}", response_model=OpportunityOut)
async def get_one(
    opportunity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Opportunity:
    """One opportunity (AT-405: lineage — detector, source record,
    timestamps — is on the response)."""
    opp = await get_opportunity_or_404(db, opportunity_id)
    await assert_opportunity_access(db, opp, current_user)
    return opp


@router.patch("/{opportunity_id}", response_model=OpportunityOut)
async def update(
    opportunity_id: int,
    payload: OpportunityUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Opportunity:
    """Update an opportunity. A status change writes an audit
    entry (AT-407)."""
    opp = await get_opportunity_or_404(db, opportunity_id)
    await assert_opportunity_owner(db, opp, current_user)
    opp = await update_opportunity(db, current_user, opp, payload)
    await db.commit()
    await db.refresh(opp)
    return opp


@router.delete(
    "/{opportunity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete(
    opportunity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an opportunity."""
    opp = await get_opportunity_or_404(db, opportunity_id)
    await assert_opportunity_owner(db, opp, current_user)
    await delete_opportunity(db, opp)
    await db.commit()


@entity_router.get(
    "/{entity_id}/opportunities", response_model=list[OpportunityOut]
)
async def list_for_entity(
    entity_id: int,
    status_filter: OpportunityStatus | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Opportunity]:
    """One entity's opportunities."""
    return await list_opportunities(
        db,
        current_user,
        entity_id=entity_id,
        status_filter=(
            status_filter.value if status_filter is not None else None
        ),
        limit=limit,
        offset=offset,
    )
