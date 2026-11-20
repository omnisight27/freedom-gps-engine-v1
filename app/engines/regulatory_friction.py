"""Regulatory Friction Score engine.

How trapped is value?

    RFS = Ownership Restrictions + Tax Friction + Capital Controls
          + Liquidity Restrictions + Compliance Burden

Implemented as the average of the five friction components, on a 0-100 scale.
Each component is scored 0-100.
"""

from __future__ import annotations


def regulatory_friction_score(
    tax_friction: float,
    compliance_friction: float,
    liquidity_friction: float,
    ownership_friction: float,
    capital_control_friction: float,
) -> float:
    """Compute the Regulatory Friction Score (0-100), rounded to 2 decimals."""
    total = (
        tax_friction
        + compliance_friction
        + liquidity_friction
        + ownership_friction
        + capital_control_friction
    )
    return round(total / 5, 2)


def regulatory_friction_rating(score: float) -> str:
    """Rating band for a Regulatory Friction Score."""
    if score < 25:
        return "Low"
    if score < 50:
        return "Moderate"
    if score < 75:
        return "High"
    return "Severe"


def calculate_regulatory_friction(
    tax_friction: float,
    compliance_friction: float,
    liquidity_friction: float,
    ownership_friction: float,
    capital_control_friction: float,
) -> dict:
    """Score and rating together."""
    score = regulatory_friction_score(
        tax_friction,
        compliance_friction,
        liquidity_friction,
        ownership_friction,
        capital_control_friction,
    )
    return {"score": score, "rating": regulatory_friction_rating(score)}
