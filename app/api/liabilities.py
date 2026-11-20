"""Liability endpoints — add a liability, list a user's liabilities."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.liability import Liability
from app.models.user import User
from app.schemas.assets import LiabilityCreate, LiabilityOut

router = APIRouter(prefix="/api/v1/liabilities", tags=["liabilities"])


@router.post("", response_model=LiabilityOut, status_code=status.HTTP_201_CREATED)
async def create_liability(
    payload: LiabilityCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Liability:
    """Add a liability to the current user's ledger."""
    if payload.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only add liabilities to your own ledger",
        )
    liability = Liability(**payload.model_dump())
    db.add(liability)
    await db.commit()
    await db.refresh(liability)
    return liability


@router.get("/{user_id}", response_model=list[LiabilityOut])
async def list_liabilities(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Liability]:
    """List a user's liabilities. A user may only list their own."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only list your own liabilities",
        )
    result = await db.execute(
        select(Liability).where(Liability.user_id == user_id)
    )
    return list(result.scalars().all())
