"""Asset endpoints — add an asset, list a user's assets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.asset import Asset
from app.models.user import User
from app.schemas.assets import AssetCreate, AssetOut

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Asset:
    """Add an asset to the current user's ledger."""
    if payload.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only add assets to your own ledger",
        )
    asset = Asset(**payload.model_dump())
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/{user_id}", response_model=list[AssetOut])
async def list_assets(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Asset]:
    """List a user's assets. A user may only list their own assets."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only list your own assets",
        )
    result = await db.execute(select(Asset).where(Asset.user_id == user_id))
    return list(result.scalars().all())
