"""API tests — public endpoints only (no database, no auth required).

Covers root, health, every score endpoint, the OmniSight economic
signals, the Shadow Ledger valuation, and the scenario stress test.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"system": "Freedom GP$", "version": "1.0"}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_scores_stinnes():
    r = client.post(
        "/scores/stinnes",
        json={
            "productive_assets": 750000,
            "hidden_assets": 250000,
            "debt_principal": 150000,
            "annual_debt_cost": 9000,
            "risk_factor": 20000,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"score": 5.59, "rating": "Asset Arbitrage"}


def test_scores_asset_flipper():
    r = client.post(
        "/scores/asset-flipper",
        json={
            "productive_assets": 500000,
            "hidden_assets": 250000,
            "total_assets": 900000,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"score": 83.33, "rating": "Wealth Engine"}


def test_scores_antifragility():
    r = client.post(
        "/scores/antifragility",
        json={"current_net_worth": 500000, "stressed_net_worth": 425000},
    )
    assert r.status_code == 200
    assert r.json() == {"score": 0.85, "rating": "Resilient"}


def test_scores_faustian():
    r = client.post(
        "/scores/faustian",
        json={"future_cost": 150000, "present_benefit": 50000},
    )
    assert r.status_code == 200
    assert r.json() == {"score": 3.0, "rating": "Dangerous"}


def test_scores_broken_safe_haven():
    r = client.post(
        "/scores/broken-safe-haven",
        json={"expected_protection": 95, "actual_protection": 62},
    )
    assert r.status_code == 200
    assert r.json() == {"score": 33, "rating": "Potentially Broken"}


def test_scores_regulatory_friction():
    r = client.post(
        "/scores/regulatory-friction",
        json={
            "tax": 40,
            "ownership": 60,
            "liquidity": 30,
            "compliance": 45,
            "capital_controls": 0,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"score": 35, "rating": "Moderate"}


def test_scores_credit_health():
    r = client.post(
        "/scores/credit-health",
        json={
            "payment_history": 0.95,
            "utilization": 0.85,
            "age": 0.80,
            "mix": 0.70,
            "inquiry": 0.90,
        },
    )
    assert r.status_code == 200
    # (0.95*0.35 + 0.85*0.30 + 0.80*0.15 + 0.70*0.10 + 0.90*0.10) * 100 = 86.75
    assert r.json()["score"] == 86.75


def test_scores_sovereignty():
    r = client.post(
        "/scores/sovereignty",
        json={"passive_income": 1500, "required_living_expenses": 2000},
    )
    assert r.status_code == 200
    assert r.json() == {"score": 0.75, "rating": "Transitional"}


def test_scores_revaluation_gap():
    r = client.post(
        "/scores/revaluation-gap",
        json={"current_value": 250000, "potential_value": 1250000},
    )
    assert r.status_code == 200
    assert r.json() == {"gap": 5.0, "rating": "Exceptional Upside"}


def test_scores_wealth_index():
    r = client.post(
        "/scores/wealth-index",
        json={
            "stinnes_score": 5.61,
            "asset_flipper_score": 83.33,
            "anti_fragility_score": 0.85,
            "sovereignty_score": 1.4,
            "regulatory_friction": 35,
            "behavioral_friction": 22,
            "faustian_ratio": 3.0,
        },
    )
    assert r.status_code == 200
    assert r.json()["wealth_index"] == 346.86


def test_omnisight_economic_signals():
    r = client.get("/api/v1/omnisight/economic-signals")
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "illustrative"
    assert data["inflation"] == 3.4
    assert data["unemployment"] == 4.1
    assert data["fed_funds_rate"] == 4.75
    assert data["m2_growth"] == 2.8
    # economic stress = 3.4*0.25 + 4.75*0.20 + 4.1*0.20 + 120*0.20 + 2.8*0.15
    assert data["economic_stress"] == 27.04


def test_shadow_ledger_value():
    r = client.post(
        "/api/v1/shadow-ledger/value",
        json={
            "education_value": 50000,
            "license_value": 75000,
            "network_value": 100000,
            "ip_value": 250000,
            "software_value": 750000,
            "publishing_value": 100000,
            "brand_value": 150000,
            "future_royalties": 250000,
            "reported_net_worth": 1200000,
        },
    )
    assert r.status_code == 200
    data = r.json()
    # Hidden assets total $1,725,000 (the spec's example).
    assert data["hidden_asset_value"] == 1725000
    # HAM = (250000 + 0 + 75000 + 100000 + 150000 + 50000 + 250000) / 1200000
    #     = 875000 / 1200000 = 0.73
    assert data["hidden_asset_multiplier"] == 0.73


def test_scenarios_stress_test_2008():
    r = client.post(
        "/api/v1/scenarios/stress-test",
        json={"scenario": "2008", "wealth_index": 850, "anti_fragility": 0.83},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["scenario"] == "2008"
    assert data["net_worth_impact"] == -28
    assert data["cash_flow_impact"] == -17
    # 850 * (1 - 0.28) = 612
    assert data["wealth_index_after_stress"] == 612
    assert data["anti_fragility"] == 0.83


def test_scenarios_stress_test_unknown():
    r = client.post(
        "/api/v1/scenarios/stress-test",
        json={"scenario": "meteor", "wealth_index": 850, "anti_fragility": 0.83},
    )
    assert r.status_code == 400


def test_state_vector_master_index_endpoint():
    """Pure computation, no DB, no auth. Illustrative state vector
    F=100, H=50, S=40, R=60, L=30, T=20, M=25, I=15, E=10.

    Prosperity = 280 x 60 / 10 = 1680.0.
    Master Index (uncalibrated placeholder coefficients, all 1.0)
    = 100+50+40+60+30+20+25+15 - 10 = 330.0.
    The response must say calibrated: false."""
    r = client.post(
        "/api/v1/state-vector/master-index",
        json={
            "financial": 100, "human": 50, "social": 40, "resource": 60,
            "legacy": 30, "trust": 20, "memory": 25, "intelligence": 15,
            "entropy": 10,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["master_index"] == 330.0
    assert data["prosperity"] == 1680.0
    assert data["calibrated"] is False
    assert "UNCALIBRATED" in data["note"]
    assert data["coefficients_used"]["alpha"] == 1.0


def test_state_vector_master_index_zero_entropy():
    """E = 0 makes prosperity undefined: prosperity is null, but the
    Master Index still computes (100+50+40+60+30+20+25+15 - 0 = 340)."""
    r = client.post(
        "/api/v1/state-vector/master-index",
        json={
            "financial": 100, "human": 50, "social": 40, "resource": 60,
            "legacy": 30, "trust": 20, "memory": 25, "intelligence": 15,
            "entropy": 0,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["prosperity"] is None
    assert data["master_index"] == 340.0
