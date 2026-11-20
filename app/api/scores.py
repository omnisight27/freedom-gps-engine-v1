"""Score endpoints — pure computation, no auth, no database.

Every endpoint takes a Pydantic request model, runs the matching engine,
and returns the score plus its rating band.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.engines.anti_fragility import calculate_anti_fragility
from app.engines.asset_flipper import calculate_asset_flipper
from app.engines.broken_safe_haven import calculate_broken_safe_haven
from app.engines.credit_health import calculate_credit_health
from app.engines.faustian import calculate_faustian
from app.engines.regulatory_friction import calculate_regulatory_friction
from app.engines.revaluation_gap import calculate_revaluation_gap
from app.engines.sovereignty import calculate_sovereignty
from app.engines.stinnes import calculate_stinnes
from app.engines.wealth import calculate_wealth_index
from app.schemas.score import (
    AntiFragilityRequest,
    AntiFragilityResponse,
    AssetFlipperRequest,
    AssetFlipperResponse,
    BrokenSafeHavenRequest,
    BrokenSafeHavenResponse,
    CreditHealthRequest,
    CreditHealthResponse,
    FaustianRequest,
    FaustianResponse,
    RegulatoryFrictionRequest,
    RegulatoryFrictionResponse,
    RevaluationGapRequest,
    RevaluationGapResponse,
    SovereigntyRequest,
    SovereigntyResponse,
    StinnesRequest,
    StinnesResponse,
    WealthIndexRequest,
    WealthIndexResponse,
)

router = APIRouter(prefix="/scores", tags=["scores"])


@router.post("/stinnes", response_model=StinnesResponse)
async def calculate_stinnes_endpoint(req: StinnesRequest) -> StinnesResponse:
    return StinnesResponse(**calculate_stinnes(**req.model_dump()))


@router.post("/asset-flipper", response_model=AssetFlipperResponse)
async def calculate_asset_flipper_endpoint(
    req: AssetFlipperRequest,
) -> AssetFlipperResponse:
    return AssetFlipperResponse(**calculate_asset_flipper(**req.model_dump()))


@router.post("/antifragility", response_model=AntiFragilityResponse)
async def calculate_antifragility_endpoint(
    req: AntiFragilityRequest,
) -> AntiFragilityResponse:
    return AntiFragilityResponse(**calculate_anti_fragility(**req.model_dump()))


@router.post("/faustian", response_model=FaustianResponse)
async def calculate_faustian_endpoint(req: FaustianRequest) -> FaustianResponse:
    return FaustianResponse(**calculate_faustian(**req.model_dump()))


@router.post("/broken-safe-haven", response_model=BrokenSafeHavenResponse)
async def calculate_broken_safe_haven_endpoint(
    req: BrokenSafeHavenRequest,
) -> BrokenSafeHavenResponse:
    return BrokenSafeHavenResponse(**calculate_broken_safe_haven(**req.model_dump()))


@router.post("/regulatory-friction", response_model=RegulatoryFrictionResponse)
async def calculate_regulatory_friction_endpoint(
    req: RegulatoryFrictionRequest,
) -> RegulatoryFrictionResponse:
    # Request field names (tax, ownership, ...) differ from the engine's
    # parameter names (tax_friction, ownership_friction, ...); map them.
    return RegulatoryFrictionResponse(
        **calculate_regulatory_friction(
            tax_friction=req.tax,
            compliance_friction=req.compliance,
            liquidity_friction=req.liquidity,
            ownership_friction=req.ownership,
            capital_control_friction=req.capital_controls,
        )
    )


@router.post("/credit-health", response_model=CreditHealthResponse)
async def calculate_credit_health_endpoint(
    req: CreditHealthRequest,
) -> CreditHealthResponse:
    return CreditHealthResponse(**calculate_credit_health(**req.model_dump()))


@router.post("/sovereignty", response_model=SovereigntyResponse)
async def calculate_sovereignty_endpoint(
    req: SovereigntyRequest,
) -> SovereigntyResponse:
    return SovereigntyResponse(**calculate_sovereignty(**req.model_dump()))


@router.post("/revaluation-gap", response_model=RevaluationGapResponse)
async def calculate_revaluation_gap_endpoint(
    req: RevaluationGapRequest,
) -> RevaluationGapResponse:
    # Request field `current_value` maps to the engine's
    # `current_recognized_value`.
    return RevaluationGapResponse(
        **calculate_revaluation_gap(
            potential_value=req.potential_value,
            current_recognized_value=req.current_value,
        )
    )


@router.post("/wealth-index", response_model=WealthIndexResponse)
async def calculate_wealth_index_endpoint(
    req: WealthIndexRequest,
) -> WealthIndexResponse:
    return WealthIndexResponse(**calculate_wealth_index(**req.model_dump()))
