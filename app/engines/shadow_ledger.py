"""Shadow Ledger engine.

The visible ledger is what a balance sheet shows. The shadow ledger is
what it leaves out.

Visible ledger:  Assets, Liabilities, Income, Expenses.
Shadow ledger:   Human Capital, Network Capital, Political Capital,
                 Brand Equity, Knowledge Capital, Future Cash Flow,
                 Regulatory Risks, Future Obligations.

Hidden Asset Multiplier:

    HAM = (IP + Skills + Licenses + Network + Brand + Education
           + Future Royalties) / Reported Net Worth
"""

from __future__ import annotations


def hidden_asset_value(
    education_value: float = 0.0,
    license_value: float = 0.0,
    network_value: float = 0.0,
    ip_value: float = 0.0,
    software_value: float = 0.0,
    publishing_value: float = 0.0,
    brand_value: float = 0.0,
    future_royalties: float = 0.0,
) -> float:
    """Total hidden-asset value: the sum of every off-balance-sheet asset."""
    return round(
        education_value
        + license_value
        + network_value
        + ip_value
        + software_value
        + publishing_value
        + brand_value
        + future_royalties,
        2,
    )


def hidden_asset_multiplier(
    ip_value: float = 0.0,
    skills_value: float = 0.0,
    licenses_value: float = 0.0,
    network_value: float = 0.0,
    brand_value: float = 0.0,
    education_value: float = 0.0,
    future_royalties: float = 0.0,
    reported_net_worth: float = 0.0,
) -> float:
    """Hidden Asset Multiplier, rounded to 2 decimals.

    Returns 0.0 when reported net worth is 0 (no base to multiply).
    """
    if reported_net_worth == 0:
        return 0.0
    total = (
        ip_value
        + skills_value
        + licenses_value
        + network_value
        + brand_value
        + education_value
        + future_royalties
    )
    return round(total / reported_net_worth, 2)


def hidden_asset_multiplier_rating(multiplier: float) -> str:
    """Rating band for a Hidden Asset Multiplier."""
    if multiplier < 0.5:
        return "Low"
    if multiplier < 1:
        return "Moderate"
    if multiplier < 2:
        return "Strong"
    return "Exceptional"
