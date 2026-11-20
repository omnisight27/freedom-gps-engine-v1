"""Tests for the Faustian Ratio engine."""

from app.engines.faustian import calculate_faustian, faustian_rating, faustian_ratio


def test_faustian_spec_example():
    """Nic's spec example: future cost 150,000; present benefit 50,000.

    Formula: 150,000 / 50,000 = 3.0.
    """
    assert faustian_ratio(150000, 50000) == 3.0
    result = calculate_faustian(150000, 50000)
    assert result == {"score": 3.0, "rating": "Dangerous"}


def test_faustian_zero_benefit():
    """No present benefit returns the sentinel 999.0."""
    assert faustian_ratio(100000, 0) == 999.0


def test_faustian_rating_bands():
    assert faustian_rating(0.5) == "Good"
    assert faustian_rating(1.5) == "Caution"
    assert faustian_rating(3.0) == "Dangerous"
    assert faustian_rating(5.0) == "Faustian"
    assert faustian_rating(9.0) == "Faustian"
