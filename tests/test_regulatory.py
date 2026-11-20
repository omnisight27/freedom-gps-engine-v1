"""Tests for the Regulatory Friction Score engine."""

from app.engines.regulatory_friction import (
    calculate_regulatory_friction,
    regulatory_friction_rating,
    regulatory_friction_score,
)


def test_regulatory_spec_example():
    """Nic's spec example: tax 40, ownership 60, liquidity 30,
    compliance 45, capital controls 0.

    Formula: (40 + 45 + 30 + 60 + 0) / 5 = 175 / 5 = 35.
    """
    assert regulatory_friction_score(40, 45, 30, 60, 0) == 35
    result = calculate_regulatory_friction(40, 45, 30, 60, 0)
    assert result == {"score": 35, "rating": "Moderate"}


def test_regulatory_zero():
    """No friction anywhere scores 0."""
    assert regulatory_friction_score(0, 0, 0, 0, 0) == 0


def test_regulatory_rating_bands():
    assert regulatory_friction_rating(10) == "Low"
    assert regulatory_friction_rating(35) == "Moderate"
    assert regulatory_friction_rating(60) == "High"
    assert regulatory_friction_rating(90) == "Severe"
