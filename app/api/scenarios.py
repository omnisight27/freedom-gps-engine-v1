"""Scenario endpoints — stress-test a portfolio against named scenarios.

Scenario impact factors are the share of net worth / cash flow a scenario
is assumed to remove (percent). They are illustrative defaults; a live
deployment calibrates them from market history.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])

# Scenario name -> (net worth impact %, cash flow impact %).
SCENARIO_IMPACTS: dict[str, tuple[float, float]] = {
    "2008": (-28.0, -17.0),
    "covid": (-20.0, -12.0),
    "inflation": (-15.0, -8.0),
}


class StressTestRequest(BaseModel):
    scenario: str = Field(description='Scenario name, e.g. "2008", "covid", "inflation"')
    wealth_index: float = Field(ge=0)
    anti_fragility: float = Field(ge=0)


class StressTestResponse(BaseModel):
    scenario: str
    net_worth_impact: float
    cash_flow_impact: float
    wealth_index_after_stress: float
    anti_fragility: float


@router.post("/stress-test", response_model=StressTestResponse)
async def stress_test(req: StressTestRequest) -> StressTestResponse:
    """Run a named stress scenario against a wealth index.

    The wealth index after stress scales with the scenario's net-worth
    impact; the anti-fragility index is reported unchanged (it is the
    profile's resilience, not the scenario's damage).
    """
    key = req.scenario.strip().lower()
    if key not in SCENARIO_IMPACTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{req.scenario}'. "
            f"Known scenarios: {sorted(SCENARIO_IMPACTS)}",
        )
    nw_impact, cf_impact = SCENARIO_IMPACTS[key]
    wealth_after = round(req.wealth_index * (1 + nw_impact / 100), 2)
    return StressTestResponse(
        scenario=req.scenario,
        net_worth_impact=nw_impact,
        cash_flow_impact=cf_impact,
        wealth_index_after_stress=wealth_after,
        anti_fragility=req.anti_fragility,
    )
