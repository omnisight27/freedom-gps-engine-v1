"""Shadow Ledger endpoints — hidden assets and the valuation engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.engines.shadow_ledger import (
    hidden_asset_multiplier,
    hidden_asset_multiplier_rating,
    hidden_asset_value,
)
from app.models.hidden_asset import HiddenAsset
from app.models.user import User
from app.schemas.assets import (
    HiddenAssetCreate,
    HiddenAssetOut,
    HiddenAssetValuationRequest,
    HiddenAssetValuationResponse,
)

router = APIRouter(prefix="/api/v1/shadow-ledger", tags=["shadow-ledger"])


@router.post("/assets", response_model=HiddenAssetOut, status_code=status.HTTP_201_CREATED)
async def create_hidden_asset(
    payload: HiddenAssetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HiddenAsset:
    """Add a hidden asset to the current user's Shadow Ledger."""
    if payload.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only add hidden assets to your own ledger",
        )
    asset = HiddenAsset(**payload.model_dump())
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.post("/value", response_model=HiddenAssetValuationResponse)
async def value_hidden_assets(
    req: HiddenAssetValuationRequest,
) -> HiddenAssetValuationResponse:
    """Value a set of hidden assets and compute the Hidden Asset Multiplier.

    Example inputs from the spec (Nic Long): education 50,000; license
    75,000; network 100,000; IP 250,000; software 750,000; publishing
    100,000; brand 150,000; future royalties 250,000 — total $1,725,000.
    """
    total = hidden_asset_value(
        education_value=req.education_value,
        license_value=req.license_value,
        network_value=req.network_value,
        ip_value=req.ip_value,
        software_value=req.software_value,
        publishing_value=req.publishing_value,
        brand_value=req.brand_value,
        future_royalties=req.future_royalties,
    )
    multiplier = hidden_asset_multiplier(
        ip_value=req.ip_value,
        skills_value=0.0,
        licenses_value=req.license_value,
        network_value=req.network_value,
        brand_value=req.brand_value,
        education_value=req.education_value,
        future_royalties=req.future_royalties,
        reported_net_worth=req.reported_net_worth,
    )
    return HiddenAssetValuationResponse(
        hidden_asset_value=total,
        hidden_asset_multiplier=multiplier,
        multiplier_rating=hidden_asset_multiplier_rating(multiplier),
    )
