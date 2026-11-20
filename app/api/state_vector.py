"""State vector endpoints — record snapshots and compute the Master Index.

POST /api/v1/state-vector records one snapshot of the caller's own
state vector Ω(t). GET returns the caller's snapshots (latest first).
POST /api/v1/state-vector/master-index is pure computation — no
database, no auth — and returns the GP$ Master Index and the Unified
Prosperity Equation for one supplied state vector.

The Master Index coefficients are explicit inputs with uncalibrated
placeholder defaults. The response always says `calibrated: false`
until real coefficients are learned statistically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.engines.master_index import calculate_master_index
from app.models.state_vector import StateVector
from app.models.user import User
from app.schemas.state_vector import (
    MasterIndexRequest,
    MasterIndexResponse,
    StateVectorCreate,
    StateVectorOut,
)

router = APIRouter(prefix="/api/v1/state-vector", tags=["state-vector"])


@router.post(
    "/master-index", response_model=MasterIndexResponse, tags=["state-vector"]
)
async def master_index_endpoint(req: MasterIndexRequest) -> MasterIndexResponse:
    """Compute the Master Index + Unified Prosperity Equation for one
    supplied state vector. Pure computation; coefficients default to
    the uncalibrated placeholders."""
    return MasterIndexResponse(
        **calculate_master_index(
            financial=req.financial,
            human=req.human,
            social=req.social,
            resource=req.resource,
            legacy=req.legacy,
            trust=req.trust,
            memory=req.memory,
            intelligence=req.intelligence,
            entropy=req.entropy,
            coefficients=req.coefficients.model_dump(),
        )
    )


@router.post(
    "", response_model=StateVectorOut, status_code=status.HTTP_201_CREATED
)
async def record_state_vector(
    payload: StateVectorCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StateVector:
    """Record one snapshot of the caller's own state vector."""
    snapshot = StateVector(user_id=current_user.id, **payload.model_dump())
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


@router.get("/{user_id}", response_model=list[StateVectorOut])
async def list_state_vectors(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StateVector]:
    """List a user's snapshots, latest first. A user may only fetch
    their own snapshots."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only fetch your own snapshots",
        )
    result = await db.execute(
        select(StateVector)
        .where(StateVector.user_id == user_id)
        .order_by(desc(StateVector.recorded_at), desc(StateVector.id))
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/{user_id}/latest", response_model=StateVectorOut)
async def latest_state_vector(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StateVector:
    """Fetch a user's latest snapshot (404 when none recorded)."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only fetch your own snapshots",
        )
    result = await db.execute(
        select(StateVector)
        .where(StateVector.user_id == user_id)
        .order_by(desc(StateVector.recorded_at), desc(StateVector.id))
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No state vector snapshot recorded yet",
        )
    return snapshot
