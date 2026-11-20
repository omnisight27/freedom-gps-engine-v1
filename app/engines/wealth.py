"""Freedom GP$ Wealth Index engine.

WEALTH != NET WORTH

FastAPI version (latest master formula):

    wealth = (Stinnes Score * 20
              + Asset Flipper Score * 2
              + Anti-Fragility Index * 100
              + Sovereignty Score * 50)

    penalties = (Regulatory Friction
                 + Behavioral Friction
                 + Faustian Ratio * 10)

    wealth_index = wealth - penalties

Note: an earlier illustrative example in the spec shows a Wealth Index of
784 for inputs (stinnes 4.88, asset flipper 82, anti-fragility 0.90,
sovereignty 1.4, behavioral friction 22). That output does NOT reconcile
with this weighted formula (it yields ~400 for those inputs), so no test
asserts 784. Tests assert the formula itself.
"""

from __future__ import annotations


def wealth_index(
    stinnes_score: float,
    asset_flipper_score: float,
    anti_fragility_score: float,
    sovereignty_score: float,
    regulatory_friction: float,
    behavioral_friction: float,
    faustian_ratio: float,
) -> float:
    """Compute the Freedom GP$ Wealth Index, rounded to 2 decimals."""
    wealth = (
        stinnes_score * 20
        + asset_flipper_score * 2
        + anti_fragility_score * 100
        + sovereignty_score * 50
    )
    penalties = (
        regulatory_friction
        + behavioral_friction
        + faustian_ratio * 10
    )
    return round(wealth - penalties, 2)


def calculate_wealth_index(
    stinnes_score: float,
    asset_flipper_score: float,
    anti_fragility_score: float,
    sovereignty_score: float,
    regulatory_friction: float,
    behavioral_friction: float,
    faustian_ratio: float,
) -> dict:
    """Wealth Index with its strengths / drags breakdown."""
    wealth = (
        stinnes_score * 20
        + asset_flipper_score * 2
        + anti_fragility_score * 100
        + sovereignty_score * 50
    )
    penalties = (
        regulatory_friction
        + behavioral_friction
        + faustian_ratio * 10
    )
    return {
        "wealth_index": round(wealth - penalties, 2),
        "strengths": round(wealth, 2),
        "penalties": round(penalties, 2),
        "components": {
            "stinnes": round(stinnes_score * 20, 2),
            "asset_flipper": round(asset_flipper_score * 2, 2),
            "anti_fragility": round(anti_fragility_score * 100, 2),
            "sovereignty": round(sovereignty_score * 50, 2),
            "regulatory_friction": round(regulatory_friction, 2),
            "behavioral_friction": round(behavioral_friction, 2),
            "faustian_ratio": round(faustian_ratio * 10, 2),
        },
    }
