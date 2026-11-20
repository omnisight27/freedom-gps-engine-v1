"""Pydantic v2 request/response models for every score endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


# --- Stinnes ---
class StinnesRequest(BaseModel):
    productive_assets: float = Field(ge=0)
    hidden_assets: float = Field(ge=0)
    debt_principal: float = Field(ge=0)
    annual_debt_cost: float = Field(ge=0)
    risk_factor: float = Field(ge=0)


class StinnesResponse(BaseModel):
    score: float
    rating: str


# --- Asset Flipper ---
class AssetFlipperRequest(BaseModel):
    productive_assets: float = Field(ge=0)
    hidden_assets: float = Field(ge=0)
    total_assets: float = Field(ge=0)


class AssetFlipperResponse(BaseModel):
    score: float
    rating: str


# --- Anti-Fragility ---
class AntiFragilityRequest(BaseModel):
    current_net_worth: float
    stressed_net_worth: float


class AntiFragilityResponse(BaseModel):
    score: float
    rating: str


# --- Faustian ---
class FaustianRequest(BaseModel):
    future_cost: float = Field(ge=0)
    present_benefit: float = Field(ge=0)


class FaustianResponse(BaseModel):
    score: float
    rating: str


# --- Broken Safe Haven ---
class BrokenSafeHavenRequest(BaseModel):
    expected_protection: float
    actual_protection: float


class BrokenSafeHavenResponse(BaseModel):
    score: float
    rating: str


# --- Regulatory Friction ---
class RegulatoryFrictionRequest(BaseModel):
    tax: float = Field(ge=0, le=100)
    ownership: float = Field(ge=0, le=100)
    liquidity: float = Field(ge=0, le=100)
    compliance: float = Field(ge=0, le=100)
    capital_controls: float = Field(ge=0, le=100)


class RegulatoryFrictionResponse(BaseModel):
    score: float
    rating: str


# --- Credit Health ---
class CreditHealthRequest(BaseModel):
    payment_history: float = Field(ge=0, le=1)
    utilization: float = Field(ge=0, le=1)
    age: float = Field(ge=0, le=1)
    mix: float = Field(ge=0, le=1)
    inquiry: float = Field(ge=0, le=1)


class CreditHealthResponse(BaseModel):
    score: float
    rating: str


# --- Sovereignty ---
class SovereigntyRequest(BaseModel):
    passive_income: float = Field(ge=0)
    required_living_expenses: float = Field(ge=0)


class SovereigntyResponse(BaseModel):
    score: float
    rating: str


# --- Revaluation Gap ---
class RevaluationGapRequest(BaseModel):
    current_value: float = Field(ge=0)
    potential_value: float = Field(ge=0)


class RevaluationGapResponse(BaseModel):
    gap: float
    rating: str


# --- Wealth Index (FastAPI version) ---
class WealthIndexRequest(BaseModel):
    stinnes_score: float
    asset_flipper_score: float
    anti_fragility_score: float
    sovereignty_score: float
    regulatory_friction: float = Field(ge=0)
    behavioral_friction: float = Field(ge=0)
    faustian_ratio: float = Field(ge=0)


class WealthIndexResponse(BaseModel):
    wealth_index: float
    strengths: float
    penalties: float
    components: dict
