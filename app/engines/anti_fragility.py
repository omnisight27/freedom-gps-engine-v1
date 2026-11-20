"""Anti-Fragility Index engine.

How do you perform during stress?

    AFI = Stress Scenario Net Worth / Current Net Worth

Bands:
    <0.50      Fragile
    0.50-0.75  Vulnerable
    0.75-1.00  Resilient
    1.00+      Anti-Fragile
"""

from __future__ import annotations


def anti_fragility_index(
    current_net_worth: float,
    stressed_net_worth: float,
) -> float:
    """Compute the Anti-Fragility Index, rounded to 2 decimals.

    Returns 0.0 when current net worth is 0 (nothing to stress-test).
    """
    if current_net_worth == 0:
        return 0.0
    return round(stressed_net_worth / current_net_worth, 2)


def anti_fragility_rating(score: float) -> str:
    """Rating band for an Anti-Fragility Index."""
    if score < 0.50:
        return "Fragile"
    if score < 0.75:
        return "Vulnerable"
    if score < 1.00:
        return "Resilient"
    return "Anti-Fragile"


def calculate_anti_fragility(
    current_net_worth: float,
    stressed_net_worth: float,
) -> dict:
    """Score and rating together."""
    score = anti_fragility_index(current_net_worth, stressed_net_worth)
    return {"score": score, "rating": anti_fragility_rating(score)}
