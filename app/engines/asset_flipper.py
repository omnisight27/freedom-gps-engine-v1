"""Asset Flipper Score engine.

How hard are assets working?

    AFS = (Productive Assets + Hidden Assets) / Total Assets * 100

Bands:
    0-20    Hoarder
    20-40   Underutilized
    40-60   Stable
    60-80   Productive
    80-100  Wealth Engine
"""

from __future__ import annotations


def asset_flipper_score(
    productive_assets: float,
    hidden_assets: float,
    total_assets: float,
) -> float:
    """Compute the Asset Flipper Score (0-100 scale), rounded to 2 decimals.

    Returns 0.0 when total assets is 0 (nothing to measure).
    """
    if total_assets == 0:
        return 0.0
    return round(((productive_assets + hidden_assets) / total_assets) * 100, 2)


def asset_flipper_rating(score: float) -> str:
    """Rating band for an Asset Flipper Score."""
    if score < 20:
        return "Hoarder"
    if score < 40:
        return "Underutilized"
    if score < 60:
        return "Stable"
    if score < 80:
        return "Productive"
    return "Wealth Engine"


def calculate_asset_flipper(
    productive_assets: float,
    hidden_assets: float,
    total_assets: float,
) -> dict:
    """Score and rating together."""
    score = asset_flipper_score(productive_assets, hidden_assets, total_assets)
    return {"score": score, "rating": asset_flipper_rating(score)}
