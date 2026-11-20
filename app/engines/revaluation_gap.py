"""Revaluation Gap engine.

How much hidden upside exists?

    RG = Potential Value / Current Recognized Value

Example: 3.0 = potential triple.
"""

from __future__ import annotations


def revaluation_gap(
    potential_value: float,
    current_recognized_value: float,
) -> float:
    """Compute the Revaluation Gap, rounded to 2 decimals.

    Returns 0.0 when the current recognized value is 0.
    """
    if current_recognized_value == 0:
        return 0.0
    return round(potential_value / current_recognized_value, 2)


def revaluation_gap_rating(gap: float) -> str:
    """Rating band for a Revaluation Gap."""
    if gap < 1:
        return "Fully Valued"
    if gap < 2:
        return "Undervalued"
    if gap < 5:
        return "Deep Upside"
    return "Exceptional Upside"


def calculate_revaluation_gap(
    potential_value: float,
    current_recognized_value: float,
) -> dict:
    """Gap and rating together."""
    gap = revaluation_gap(potential_value, current_recognized_value)
    return {"gap": gap, "rating": revaluation_gap_rating(gap)}
