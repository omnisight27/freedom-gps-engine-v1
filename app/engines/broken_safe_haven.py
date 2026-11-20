"""Broken Safe Haven Index engine.

Trust versus actual protection.

    BSHI = Expected Crisis Protection - Actual Crisis Protection

A positive gap means the haven protected less than expected.
A zero or negative gap means it held up as promised (or better).
"""

from __future__ import annotations


def broken_safe_haven_index(
    expected_protection: float,
    actual_protection: float,
) -> float:
    """Compute the Broken Safe Haven Index, rounded to 2 decimals."""
    return round(expected_protection - actual_protection, 2)


def broken_safe_haven_rating(score: float) -> str:
    """Rating band for a Broken Safe Haven Index."""
    if score <= 0:
        return "Intact"
    if score < 20:
        return "Mostly Intact"
    if score < 50:
        return "Potentially Broken"
    return "Broken"


def calculate_broken_safe_haven(
    expected_protection: float,
    actual_protection: float,
) -> dict:
    """Score and rating together."""
    score = broken_safe_haven_index(expected_protection, actual_protection)
    return {"score": score, "rating": broken_safe_haven_rating(score)}
