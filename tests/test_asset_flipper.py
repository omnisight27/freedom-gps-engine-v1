"""Tests for the Asset Flipper Score engine."""

from app.engines.asset_flipper import (
    asset_flipper_rating,
    asset_flipper_score,
    calculate_asset_flipper,
)


def test_asset_flipper_spec_example():
    """Nic's spec example: productive 500,000; hidden 250,000; total 900,000.

    Formula: (500,000 + 250,000) / 900,000 * 100 = 83.33.
    """
    assert asset_flipper_score(500000, 250000, 900000) == 83.33
    result = calculate_asset_flipper(500000, 250000, 900000)
    assert result == {"score": 83.33, "rating": "Wealth Engine"}


def test_asset_flipper_zero_total():
    """No assets at all returns 0.0."""
    assert asset_flipper_score(0, 0, 0) == 0.0


def test_asset_flipper_rating_bands():
    assert asset_flipper_rating(10) == "Hoarder"
    assert asset_flipper_rating(30) == "Underutilized"
    assert asset_flipper_rating(50) == "Stable"
    assert asset_flipper_rating(70) == "Productive"
    assert asset_flipper_rating(83.33) == "Wealth Engine"
    assert asset_flipper_rating(95) == "Wealth Engine"
