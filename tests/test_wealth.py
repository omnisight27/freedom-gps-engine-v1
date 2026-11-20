"""Tests for the Freedom GP$ Wealth Index engine.

FastAPI version (latest master formula):

    wealth = (stinnes * 20 + asset_flipper * 2
              + anti_fragility * 100 + sovereignty * 50)
    penalties = (regulatory_friction + behavioral_friction
                 + faustian_ratio * 10)
    wealth_index = wealth - penalties

IMPORTANT: the spec's illustrative example lists a Wealth Index of 784
for inputs (stinnes 4.88, asset flipper 82, anti-fragility 0.90,
sovereignty 1.4, behavioral friction 22). That output does NOT reconcile
with this weighted formula (it yields ~335-400 for those inputs), so no
test asserts 784. These tests assert the FORMULA.
"""

from app.engines.wealth import calculate_wealth_index, wealth_index


def test_wealth_index_formula():
    """Hand-computed from the weighted formula.

    Inputs: stinnes 5.61, asset flipper 83.33, anti-fragility 0.85,
    sovereignty 1.4, regulatory friction 35, behavioral friction 22,
    faustian ratio 3.0.

    wealth = 5.61*20 + 83.33*2 + 0.85*100 + 1.4*50
           = 112.2 + 166.66 + 85 + 70 = 433.86
    penalties = 35 + 22 + 3.0*10 = 87
    wealth_index = 433.86 - 87 = 346.86
    """
    result = wealth_index(5.61, 83.33, 0.85, 1.4, 35, 22, 3.0)
    assert result == 346.86


def test_wealth_index_breakdown():
    """The breakdown exposes strengths, penalties, and components."""
    result = calculate_wealth_index(5.61, 83.33, 0.85, 1.4, 35, 22, 3.0)
    assert result["wealth_index"] == 346.86
    assert result["strengths"] == 433.86
    assert result["penalties"] == 87
    assert result["components"]["stinnes"] == 112.2
    assert result["components"]["asset_flipper"] == 166.66
    assert result["components"]["anti_fragility"] == 85.0
    assert result["components"]["sovereignty"] == 70.0
    assert result["components"]["faustian_ratio"] == 30.0


def test_wealth_index_spec_example_does_not_yield_784():
    """The spec's 784 example does not reconcile with the formula.

    Inputs from the spec example: stinnes 4.88, asset flipper 82,
    anti-fragility 0.90, sovereignty 1.4, behavioral friction 22,
    regulatory friction 35, faustian ratio 3.0.

    wealth = 4.88*20 + 82*2 + 0.90*100 + 1.4*50 = 421.6
    penalties = 35 + 22 + 30 = 87
    wealth_index = 421.6 - 87 = 334.6  (not 784)
    """
    result = wealth_index(4.88, 82, 0.90, 1.4, 35, 22, 3.0)
    assert result == 334.6
    assert result != 784
