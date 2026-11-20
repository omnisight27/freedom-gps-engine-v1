"""Pydantic v2 models for assets, liabilities, and hidden assets."""

from __future__ import annotations

from pydantic import BaseModel, Field


# --- Asset ---
class AssetCreate(BaseModel):
    user_id: int
    type: str
    name: str
    value: float = 0.0
    cash_flow: float = 0.0
    appreciating: bool = False


class AssetOut(BaseModel):
    id: int
    user_id: int
    type: str
    name: str
    value: float
    cash_flow: float
    appreciating: bool

    model_config = {"from_attributes": True}


# --- Liability ---
class LiabilityCreate(BaseModel):
    user_id: int
    type: str
    name: str
    balance: float = 0.0
    interest_rate: float = 0.0


class LiabilityOut(BaseModel):
    id: int
    user_id: int
    type: str
    name: str
    balance: float
    interest_rate: float

    model_config = {"from_attributes": True}


# --- Hidden Asset (Shadow Ledger) ---
class HiddenAssetCreate(BaseModel):
    user_id: int
    type: str
    name: str
    estimated_value: float = 0.0


class HiddenAssetOut(BaseModel):
    id: int
    user_id: int
    type: str
    name: str
    estimated_value: float

    model_config = {"from_attributes": True}


# --- Hidden Asset Valuation (the valuation engine inputs) ---
class HiddenAssetValuationRequest(BaseModel):
    education_value: float = Field(ge=0, default=0.0)
    license_value: float = Field(ge=0, default=0.0)
    network_value: float = Field(ge=0, default=0.0)
    ip_value: float = Field(ge=0, default=0.0)
    software_value: float = Field(ge=0, default=0.0)
    publishing_value: float = Field(ge=0, default=0.0)
    brand_value: float = Field(ge=0, default=0.0)
    future_royalties: float = Field(ge=0, default=0.0)
    reported_net_worth: float = Field(ge=0, default=0.0)


class HiddenAssetValuationResponse(BaseModel):
    hidden_asset_value: float
    hidden_asset_multiplier: float
    multiplier_rating: str
