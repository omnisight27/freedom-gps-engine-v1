"""Sovereignty Score engine.

Can you live independent of a paycheck?

    Sovereignty Score = Passive Income / Required Living Expenses

Bands:
    0-0.5   Dependent
    0.5-1   Transitional
    1-2     Sovereign
    2+      Fully Sovereign
"""

from __future__ import annotations


def sovereignty_score(
    passive_income: float,
    required_living_expenses: float,
) -> float:
    """Compute the Sovereignty Score, rounded to 2 decimals.

    Returns 999.0 when there are no required living expenses.
    """
    if required_living_expenses == 0:
        return 999.0
    return round(passive_income / required_living_expenses, 2)


def sovereignty_rating(score: float) -> str:
    """Rating band for a Sovereignty Score."""
    if score < 0.5:
        return "Dependent"
    if score < 1:
        return "Transitional"
    if score < 2:
        return "Sovereign"
    return "Fully Sovereign"


def calculate_sovereignty(
    passive_income: float,
    required_living_expenses: float,
) -> dict:
    """Score and rating together."""
    score = sovereignty_score(passive_income, required_living_expenses)
    return {"score": score, "rating": sovereignty_rating(score)}
