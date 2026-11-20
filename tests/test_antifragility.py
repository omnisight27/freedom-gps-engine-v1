"""Tests for the Anti-Fragility Index engine."""

from app.engines.anti_fragility import (
    anti_fragility_index,
    anti_fragility_rating,
    calculate_anti_fragility,
)


def test_antifragility_spec_example():
    """Nic's spec example: current net worth 500,000; stressed 425,000.

    Formula: 425,000 / 500,000 = 0.85.
    """
    assert anti_fragility_index(500000, 425000) == 0.85
    result = calculate_anti_fragility(500000, 425000)
    assert result == {"score": 0.85, "rating": "Resilient"}


def test_antifragility_zero_current():
    """No current net worth returns 0.0."""
    assert anti_fragility_index(0, 0) == 0.0


def test_antifragility_rating_bands():
    assert anti_fragility_rating(0.40) == "Fragile"
    assert anti_fragility_rating(0.60) == "Vulnerable"
    assert anti_fragility_rating(0.85) == "Resilient"
    assert anti_fragility_rating(1.00) == "Anti-Fragile"
    assert anti_fragility_rating(1.20) == "Anti-Fragile"
