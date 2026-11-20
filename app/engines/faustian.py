"""Faustian Ratio engine.

Future pain vs current gain.

    FR = Lifetime Cost / Immediate Benefit

Bands:
    <1   Good
    1-2  Caution
    2-5  Dangerous
    5+   Faustian
"""

from __future__ import annotations


def faustian_ratio(
    future_cost: float,
    present_benefit: float,
) -> float:
    """Compute the Faustian Ratio, rounded to 2 decimals.

    Returns 999.0 when there is no present benefit (all future pain).
    """
    if present_benefit == 0:
        return 999.0
    return round(future_cost / present_benefit, 2)


def faustian_rating(ratio: float) -> str:
    """Rating band for a Faustian Ratio."""
    if ratio < 1:
        return "Good"
    if ratio < 2:
        return "Caution"
    if ratio < 5:
        return "Dangerous"
    return "Faustian"


def calculate_faustian(
    future_cost: float,
    present_benefit: float,
) -> dict:
    """Score and rating together."""
    score = faustian_ratio(future_cost, present_benefit)
    return {"score": score, "rating": faustian_rating(score)}
