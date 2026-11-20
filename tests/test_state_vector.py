"""Tests for the state vector Ω(t), the GP$ Master Index, and the
Unified Prosperity Equation.

Illustrative values used throughout (small, clearly labeled teaching
numbers — not real figures):

    F=100, H=50, S=40, R=60, L=30, T=20, M=25, I=15, E=10

Hand-computed expected values:

    Prosperity P = (F+H+S+R+L) × (T+I+M) / E
                 = (100+50+40+60+30) × (20+25+15) / 10
                 = 280 × 60 / 10 = 1680.0

    Master Index (all coefficients 1.0, the uncalibrated placeholders):
                 = 100+50+40+60+30+20+25+15 − 10 = 330.0

    Master Index with alpha=2, all others 1.0:
                 = 200+50+40+60+30+20+25+15 − 10 = 430.0

The DB-backed endpoint tests use an in-memory SQLite database via
aiosqlite (a test-only dependency — it is NOT used in production, which
runs PostgreSQL). They exercise the real auth path: create a user, log
in, record snapshots with the JWT, then list and fetch the latest.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.engines.master_index import (
    DEFAULT_COEFFICIENTS,
    calculate_master_index,
    master_index,
    unified_prosperity,
)
from app.main import app
from app.models.state_vector import StateVector
from app.schemas.state_vector import StateVectorCreate, StateVectorOut

F, H, S, R, L, T, M, I, E = 100, 50, 40, 60, 30, 20, 25, 15, 10


# --- Engine: Unified Prosperity Equation ---
def test_prosperity_formula():
    """P = (F+H+S+R+L) × (T+I+M) / E = 280 × 60 / 10 = 1680.0."""
    assert unified_prosperity(F, H, S, R, L, T, M, I, E) == 1680.0


def test_prosperity_zero_entropy_returns_none():
    """E = 0 makes the equation undefined: no value is invented."""
    assert unified_prosperity(F, H, S, R, L, T, M, I, 0) is None


def test_prosperity_negative_financial():
    """Financial capital may be negative (liabilities exceed assets).

    Capitals = -50+50+40+60+30 = 130; amplifiers = 60; 130 × 60 / 10.
    """
    assert unified_prosperity(-50, H, S, R, L, T, M, I, E) == 780.0


# --- Engine: GP$ Master Index ---
def test_master_index_equal_weights():
    """All coefficients 1.0: 100+50+40+60+30+20+25+15 − 10 = 330.0."""
    assert master_index(F, H, S, R, L, T, M, I, E) == 330.0


def test_master_index_custom_coefficient():
    """alpha=2, all others 1.0: 200+50+40+60+30+20+25+15 − 10 = 430.0."""
    result = master_index(F, H, S, R, L, T, M, I, E, {"alpha": 2.0})
    assert result == 430.0


def test_master_index_defaults_are_uncalibrated_placeholders():
    """Every default coefficient is 1.0 — equal weight, uncalibrated."""
    assert set(DEFAULT_COEFFICIENTS) == {
        "alpha", "beta", "gamma", "delta", "epsilon",
        "zeta", "eta", "theta", "lam",
    }
    assert all(v == 1.0 for v in DEFAULT_COEFFICIENTS.values())


def test_calculate_master_index_breakdown():
    result = calculate_master_index(F, H, S, R, L, T, M, I, E)
    assert result["master_index"] == 330.0
    assert result["prosperity"] == 1680.0
    assert result["components"]["financial"] == 100.0
    assert result["components"]["entropy"] == -10.0
    assert result["coefficients_used"] == DEFAULT_COEFFICIENTS
    # Never presented as calibrated until real coefficients are learned.
    assert result["calibrated"] is False
    assert "UNCALIBRATED" in result["note"]


# --- Schema / model round-trip (no database) ---
def test_state_vector_schema_round_trip():
    """Create → model instance → response model, values intact."""
    payload = StateVectorCreate(
        financial=F, human=H, social=S, resource=R, legacy=L,
        trust=T, memory=M, intelligence=I, entropy=E,
        opportunity=25, notes="illustrative snapshot",
    )
    instance = StateVector(
        id=1,
        user_id=1,
        recorded_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        **payload.model_dump(),
    )
    out = StateVectorOut.model_validate(instance)
    assert out.financial == 100
    assert out.human == 50
    assert out.entropy == 10
    assert out.opportunity == 25
    assert out.notes == "illustrative snapshot"


def test_state_vector_schema_rejects_negative_magnitude():
    """Magnitudes (human, trust, entropy, ...) cannot be negative.
    Financial capital can be (liabilities may exceed assets)."""
    with pytest.raises(Exception):
        StateVectorCreate(human=-1)
    ok = StateVectorCreate(financial=-500, human=0)
    assert ok.financial == -500


def test_state_vector_model_table():
    assert StateVector.__tablename__ == "state_vectors"
    assert "financial" in StateVector.__table__.columns
    assert "entropy" in StateVector.__table__.columns
    assert "opportunity" in StateVector.__table__.columns


# --- DB-backed endpoint tests (in-memory SQLite, real auth path) ---
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
_test_session = async_sessionmaker(
    bind=_test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _test_session() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def _test_db():
    async def _init():
        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


def _auth_client() -> tuple[TestClient, int]:
    """Create a fresh user (unique email per call), log in, return
    (client, user_id) with the token set."""
    import uuid

    email = f"illustrative-{uuid.uuid4().hex[:8]}@example.com"
    client = TestClient(app)
    r = client.post(
        "/api/v1/users",
        json={"name": "Illustrative User", "email": email, "password": "test-password-123"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    r = client.post(
        "/api/v1/users/login",
        data={"username": email, "password": "test-password-123"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def test_record_and_list_state_vectors():
    client, user_id = _auth_client()
    payload = {
        "financial": F, "human": H, "social": S, "resource": R, "legacy": L,
        "trust": T, "memory": M, "intelligence": I, "entropy": E,
        "opportunity": 25, "notes": "illustrative snapshot",
    }
    r = client.post("/api/v1/state-vector", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_id"] == user_id
    assert body["financial"] == 100
    assert body["entropy"] == 10

    r = client.get(f"/api/v1/state-vector/{user_id}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]

    r = client.get(f"/api/v1/state-vector/{user_id}/latest")
    assert r.status_code == 200
    assert r.json()["id"] == body["id"]


def test_state_vector_forbidden_for_other_user():
    client, user_id = _auth_client()
    r = client.get(f"/api/v1/state-vector/{user_id + 999}")
    assert r.status_code == 403


def test_state_vector_requires_auth():
    client = TestClient(app)
    r = client.post("/api/v1/state-vector", json={"financial": 100})
    assert r.status_code == 401
