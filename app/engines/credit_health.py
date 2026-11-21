"""Credit Health Score engine.

Can you borrow?

    CHS = (Payment History * 0.35 + Utilization * 0.30 + Age * 0.15
           + Mix * 0.10 + Inquiry Score * 0.10) * 850

Scale 0-850 (FICO-style). Component inputs are 0-1 fractions; the
weighted sum is 0-1 and the * 850 factor puts the result on the 0-850
scale. A perfect profile scores 850.
"""

from __future__ import annotations

MAX_SCORE = 850.0


def credit_health_score(
    payment_history: float,
    utilization: float,
    age: float,
    mix: float,
    inquiry: float,
) -> float:
    """Compute the Credit Health Score, rounded to 2 decimals.

    Each component is a 0-1 fraction. Result is clamped to 0-850.
    """
    weighted = (
        payment_history * 0.35
        + utilization * 0.30
        + age * 0.15
        + mix * 0.10
        + inquiry * 0.10
    )
    score = weighted * 850
    return round(max(0.0, min(MAX_SCORE, score)), 2)


def credit_health_rating(score: float) -> str:
    """Rating band for a Credit Health Score (0-850 scale)."""
    if score < 580:
        return "Poor"
    if score < 670:
        return "Fair"
    if score < 740:
        return "Good"
    if score < 800:
        return "Very Good"
    return "Excellent"


def calculate_credit_health(
    payment_history: float,
    utilization: float,
    age: float,
    mix: float,
    inquiry: float,
) -> dict:
    """Score and rating together."""
    score = credit_health_score(payment_history, utilization, age, mix, inquiry)
    return {"score": score, "rating": credit_health_rating(score)}
