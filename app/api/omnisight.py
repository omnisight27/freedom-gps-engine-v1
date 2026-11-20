"""OmniSight endpoints — economic signals and the economic stress score.

The signals returned here are ILLUSTRATIVE demo values, not live data.
A live deployment wires these to real feeds (FRED, Treasury, market data)
and the OmniSight Intelligence Layer. The economic stress score itself is
computed from the formula, so it is correct for whatever inputs arrive.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/omnisight", tags=["omnisight"])


def economic_stress_score(
    inflation: float,
    rates: float,
    unemployment: float,
    debt_to_gdp: float,
    money_supply_growth: float,
) -> float:
    """OmniSight Economic Stress Score.

        score = inflation * 0.25 + rates * 0.20 + unemployment * 0.20
                + debt_to_gdp * 0.20 + money_supply_growth * 0.15
    """
    score = (
        inflation * 0.25
        + rates * 0.20
        + unemployment * 0.20
        + debt_to_gdp * 0.20
        + money_supply_growth * 0.15
    )
    return round(score, 2)


@router.get("/economic-signals")
async def get_economic_signals(
    inflation: float = Query(3.4, description="Inflation rate, percent"),
    unemployment: float = Query(4.1, description="Unemployment rate, percent"),
    fed_funds_rate: float = Query(4.75, description="Federal funds rate, percent"),
    m2_growth: float = Query(2.8, description="M2 money supply growth, percent"),
    debt_to_gdp: float = Query(120.0, description="Debt-to-GDP ratio, percent"),
) -> dict:
    """Current economic signals with the computed stress score.

    Defaults are illustrative demo values. ``source`` is always reported
    so a consumer never mistakes demo values for a live feed.
    """
    return {
        "inflation": inflation,
        "unemployment": unemployment,
        "fed_funds_rate": fed_funds_rate,
        "m2_growth": m2_growth,
        "debt_to_gdp": debt_to_gdp,
        "economic_stress": economic_stress_score(
            inflation, fed_funds_rate, unemployment, debt_to_gdp, m2_growth
        ),
        "source": "illustrative",
    }
