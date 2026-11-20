"""Tests for the Stinnes Score engine."""

from app.engines.stinnes import calculate_stinnes, stinnes_rating, stinnes_score


def test_stinnes_spec_example():
    """Nic's spec example inputs.

    productive 750,000; hidden 250,000; debt principal 150,000;
    annual carry cost 9,000; risk factor 20,000.

    Formula: (750,000 + 250,000) / (150,000 + 9,000 + 20,000)
             = 1,000,000 / 179,000 = 5.59.

    NOTE: the spec's illustrative example lists 5.61 for these inputs, but
    the formula yields 5.59. The test asserts the FORMULA. The rating is
    "Asset Arbitrage" either way (4-8 band).
    """
    assert stinnes_score(750000, 250000, 150000, 9000, 20000) == 5.59
    result = calculate_stinnes(750000, 250000, 150000, 9000, 20000)
    assert result == {"score": 5.59, "rating": "Asset Arbitrage"}


def test_stinnes_zero_denominator():
    """No debt burden at all returns the sentinel 999.0."""
    assert stinnes_score(100000, 50000, 0, 0, 0) == 999.0


def test_stinnes_rating_bands():
    assert stinnes_rating(0.5) == "Debt Trap"
    assert stinnes_rating(1.5) == "Stable"
    assert stinnes_rating(3.0) == "Productive"
    assert stinnes_rating(5.59) == "Asset Arbitrage"
    assert stinnes_rating(8.0) == "Financial Sovereignty"
    assert stinnes_rating(12.0) == "Financial Sovereignty"
