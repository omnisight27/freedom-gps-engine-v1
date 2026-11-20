"""Stinnes Score engine.

Is debt creating wealth?

    SS = (Cash Flow Assets + Appreciating Assets + Hidden Assets)
         / (Debt Principal + Debt Carry Cost + Debt Risk)

Bands:
    <1.0  Debt Trap
    1-2   Stable
    2-4   Productive
    4-8   Asset Arbitrage
    8+    Financial Sovereignty
"""

from __future__ import annotations


def stinnes_score(
    productive_assets: float,
    hidden_assets: float,
    debt_principal: float,
    annual_debt_cost: float,
    risk_factor: float,
) -> float:
    """Compute the Stinnes Score, rounded to 2 decimals.

    Returns 999.0 when the denominator is 0 (no debt burden at all).
    """
    denominator = debt_principal + annual_debt_cost + risk_factor
    if denominator == 0:
        return 999.0
    numerator = productive_assets + hidden_assets
    return round(numerator / denominator, 2)


def stinnes_rating(score: float) -> str:
    """Rating band for a Stinnes Score."""
    if score < 1:
        return "Debt Trap"
    if score < 2:
        return "Stable"
    if score < 4:
        return "Productive"
    if score < 8:
        return "Asset Arbitrage"
    return "Financial Sovereignty"


def calculate_stinnes(
    productive_assets: float,
    hidden_assets: float,
    debt_principal: float,
    annual_debt_cost: float,
    risk_factor: float,
) -> dict:
    """Score and rating together."""
    score = stinnes_score(
        productive_assets, hidden_assets, debt_principal, annual_debt_cost, risk_factor
    )
    return {"score": score, "rating": stinnes_rating(score)}
