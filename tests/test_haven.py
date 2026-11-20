"""Tests for the Broken Safe Haven Index engine."""

from app.engines.broken_safe_haven import (
    broken_safe_haven_index,
    broken_safe_haven_rating,
    calculate_broken_safe_haven,
)


def test_haven_spec_example():
    """Nic's spec example: expected protection 95; actual protection 62.

    Formula: 95 - 62 = 33.
    """
    assert broken_safe_haven_index(95, 62) == 33
    result = calculate_broken_safe_haven(95, 62)
    assert result == {"score": 33, "rating": "Potentially Broken"}


def test_haven_intact():
    """A haven that protected as promised scores 0 or less."""
    assert broken_safe_haven_index(80, 80) == 0
    assert broken_safe_haven_rating(0) == "Intact"
    assert broken_safe_haven_index(70, 85) == -15
    assert broken_safe_haven_rating(-15) == "Intact"


def test_haven_rating_bands():
    assert broken_safe_haven_rating(10) == "Mostly Intact"
    assert broken_safe_haven_rating(33) == "Potentially Broken"
    assert broken_safe_haven_rating(60) == "Broken"
