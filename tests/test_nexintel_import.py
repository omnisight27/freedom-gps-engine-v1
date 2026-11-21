"""Tests for Integration Sprint A / EO 007A Phase A — the OmniSight
-> Freedom GP$ connector: NexIntel history -> Validation Registry.

The import loads the NexIntel loop's public history.json through
the engine's existing write paths (vault endpoint for
KnowledgeAssets, registry services for Predictions / Outcomes /
CalibrationRecords). These tests use a small synthetic history
document that mirrors the real file's schema — the real 3.4 MB
file is verified separately by running the CLI against a scratch
database, not by the unit suite.

The DB-backed tests use the same harness as the Sprint 5 suite:
an in-memory SQLite database via aiosqlite, the real auth path
(create a user, log in, work with the JWT). Each test uses a
fresh entity so counts are isolated per test; the NexIntel model
row is shared (get-or-created by name) and asserted by name.
"""

from __future__ import annotations

import asyncio
import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.entity import Entity
from app.models.graph import GraphNode
from app.models.registry import (
    CalibrationRecord,
    Outcome,
    Prediction,
    RegistryModel,
)
from app.models.user import User
from app.models.vault import KnowledgeAsset, KnowledgeCategory
from app.services.nexintel_import import (
    AGGREGATE_SOURCE_KEY,
    NEXINTEL_MODEL_NAME,
    import_nexintel_history,
    pick_source_key,
)


# --- Harness (same pattern as the Sprint 5 suite) ---
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)


@event.listens_for(_test_engine.sync_engine, "connect")
def _enable_sqlite_fk(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
    email = f"illustrative-{uuid.uuid4().hex[:8]}@example.com"
    client = TestClient(app)
    r = client.post(
        "/api/v1/users",
        json={
            "name": "Illustrative User",
            "email": email,
            "password": "test-password-123",
        },
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


def _make_entity(client: TestClient, name: str) -> dict:
    r = client.post("/api/v1/entities", json={"type": "person", "name": name})
    assert r.status_code == 201, r.text
    return r.json()


# --- Synthetic history (mirrors the real history.json schema) ---
def _pick(
    asset: str,
    *,
    direction: str = "LONG",
    asset_class: str = "crypto",
    price: float = 100.0,
    score: float = 100.0,
    confidence: int = 70,
    expected: float = 8.0,
    win=None,
    return_pct=None,
    exit_price=None,
    excluded: bool = False,
) -> dict:
    pick = {
        "asset": asset,
        "price": price,
        "score": score,
        "confidence": confidence,
        "reason": f"{asset} test pick.",
        "class": asset_class,
        "name": asset,
        "thesis": {
            "direction": direction,
            "stop_loss": price * 0.95,
            "take_profit": price * 1.1,
            "risk_reward": 1.33,
            "time_horizon": "1-5 days (swing)",
            "entry_date": "2026-11-18",
            "projected_exit": "2026-11-21",
            "expected_return_pct": expected,
            "max_loss_pct": 5.0,
        },
        "regime": "NEUTRAL",
        "calib_band": "70-79",
        "exit": exit_price,
        "return_pct": return_pct,
        "win": win,
    }
    if excluded:
        pick["excluded"] = True
    return pick


def _history() -> dict:
    """Two runs: a graded long win, a graded long loss, an
    ungraded short, an excluded known-bad pick, and the
    duplicate-class case (two CVX shorts in one run — crypto
    Convex Finance and equity Chevron)."""
    return {
        "stats": {"wins": 3, "losses": 1, "total": 4, "win_rate": 75.0},
        "error": {
            "sum_abs_err": 16.5,
            "n": 3,
            "within_5pct": 1,
            "metric": "return_pct_v1",
            "mae": 5.5,
            "within_5pct_rate": 33.3,
        },
        "calibration": [
            {"band": "70-79", "n": 2, "win_rate": 50.0},
        ],
        "calibration_v2": [],
        "corrections": [
            {
                "date": "2026-11-18T00:00:00+00:00",
                "action": "test correction",
                "picks_removed": 1,
            }
        ],
        "picks": [
            {
                "date": "2026-11-18T12:00:00+00:00",
                "scored": True,
                "longs": [
                    _pick(
                        "BTC",
                        price=90000.0,
                        score=500.0,
                        confidence=72,
                        expected=8.0,
                        win=True,
                        return_pct=12.5,
                        exit_price=101250.0,
                    ),
                    _pick(
                        "ETH",
                        price=3000.0,
                        score=300.0,
                        confidence=65,
                        expected=6.0,
                        win=False,
                        return_pct=-3.2,
                        exit_price=2904.0,
                    ),
                    # Regression case for the value-matching bug:
                    # DOGE shares BTC's expected return (8.0) and
                    # confidence (72) in the same run. It must
                    # still be its own prediction, not BTC's.
                    _pick(
                        "DOGE",
                        price=0.10,
                        score=450.0,
                        confidence=72,
                        expected=8.0,
                        win=True,
                        return_pct=9.0,
                        exit_price=0.109,
                    ),
                    _pick(
                        "CVX",
                        price=2.44,
                        score=664.2,
                        confidence=78,
                        expected=4.0,
                        win=True,
                        return_pct=9457.79,
                        exit_price=233.21,
                        excluded=True,
                    ),
                ],
                "shorts": [
                    _pick(
                        "SOL",
                        direction="SHORT",
                        price=200.0,
                        score=-150.0,
                        confidence=80,
                        expected=10.0,
                    ),
                ],
                "fear_greed": 51,
                "regime": "NEUTRAL",
            },
            {
                "date": "2026-11-19T12:00:00+00:00",
                "scored": True,
                "longs": [],
                "shorts": [
                    _pick(
                        "CVX",
                        direction="SHORT",
                        asset_class="crypto",
                        price=2.49,
                        score=-44.7,
                        confidence=78,
                        expected=5.0,
                        win=True,
                        return_pct=2.8,
                        exit_price=2.42,
                    ),
                    _pick(
                        "CVX",
                        direction="SHORT",
                        asset_class="equity",
                        price=233.21,
                        score=-22.7,
                        confidence=77,
                        expected=4.0,
                    ),
                ],
                "fear_greed": 52,
                "regime": "NEUTRAL",
            },
        ],
    }


async def _run_import(
    history: dict, entity_id: int, user_id: int
) -> dict:
    """Run the import against the test database and return its
    counts."""
    async with _test_session() as session:
        user = (
            await session.execute(
                select(User).where(User.id == user_id)
            )
        ).scalar_one()
        entity = (
            await session.execute(
                select(Entity).where(Entity.id == entity_id)
            )
        ).scalar_one()
        return await import_nexintel_history(session, user, entity, history)


async def _rows(model, **where):
    async with _test_session() as session:
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.execute(stmt)).scalars().all())


# --- The connector ---
def test_prediction_category_exists():
    """The connector's category: a NexIntel pick is a Prediction
    record, not Research."""
    assert KnowledgeCategory.PREDICTION.value == "Prediction"
    assert "Prediction" in {c.value for c in KnowledgeCategory}


def test_import_creates_full_chain():
    """One graded pick -> asset + prediction + outcome +
    calibration, with CE computed server-side; ungraded picks
    stop at prediction; the aggregate asset records the loop's
    own summary."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NexIntel Import Entity A")
    history = _history()
    counts = asyncio.run(_run_import(history, entity["id"], user_id))

    assert counts["runs_seen"] == 2
    assert counts["picks_seen"] == 7
    assert counts["graded_seen"] == 4
    assert counts["ungraded_seen"] == 2
    assert counts["skipped_excluded"] == 1
    assert counts["knowledge_assets_created"] == 6
    assert counts["predictions_created"] == 6
    assert counts["outcomes_created"] == 4
    assert counts["calibrations_created"] == 4
    assert counts["aggregate_created"] == 1

    # The model row exists exactly once, by name.
    models = asyncio.run(_rows(RegistryModel, name=NEXINTEL_MODEL_NAME))
    assert len(models) == 1
    assert models[0].validation_level == "Experimental"

    # CE = observed - predicted, server-side: BTC 12.5-8.0=4.5,
    # DOGE 9.0-8.0=1.0, ETH -3.2-6.0=-9.2, CVX-crypto 2.8-5.0=-2.2.
    # Two calibrations share predicted 8.0 — they are distinct
    # records, not one merged row.
    calibrations = asyncio.run(_rows(CalibrationRecord))
    assert sorted(
        (c.predicted_value, c.error) for c in calibrations
    ) == sorted(
        [(8.0, 4.5), (8.0, 1.0), (6.0, -9.2), (5.0, -2.2)]
    )
    assert all(c.model_id == models[0].model_id for c in calibrations)

    # Predictions are dated when the loop made the pick, and
    # carry the pick's confidence on a 0-1 scale.
    predictions = asyncio.run(
        _rows(Prediction, entity_id=entity["id"])
    )
    assert len(predictions) == 6
    assert {p.forecast_horizon for p in predictions} == {"5d"}
    assert {p.confidence for p in predictions} == {
        0.72,
        0.65,
        0.8,
        0.78,
        0.77,
    }
    assert {p.prediction_date.date().isoformat() for p in predictions} == {
        "2026-11-18",
        "2026-11-19",
    }
    # The predicted_outcome carries the pick's identity: BTC and
    # DOGE share expected 8.0 and confidence 0.72 in one run and
    # are still two predictions with two source keys.
    outcomes_by_key = {
        p.predicted_outcome["source_key"]: p
        for p in predictions
        if isinstance(p.predicted_outcome, dict)
    }
    assert len(outcomes_by_key) == 6
    assert all(
        p.predicted_outcome["value"] is not None
        for p in outcomes_by_key.values()
    )
    btc = outcomes_by_key[
        "nexintel:2026-11-18T12:00:00+00:00:LONG:BTC:crypto"
    ]
    doge = outcomes_by_key[
        "nexintel:2026-11-18T12:00:00+00:00:LONG:DOGE:crypto"
    ]
    assert btc.prediction_id != doge.prediction_id
    assert btc.predicted_outcome["value"] == 8.0
    assert doge.predicted_outcome["value"] == 8.0

    # Every pick asset got its graph node (type KnowledgeAsset)
    # through the Sprint 3 auto-sync; the aggregate too.
    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    assert len(assets) == 7
    assert {a.category for a in assets} == {
        "Prediction",
        "Historical Record",
    }
    asset_ids = {a.id for a in assets}
    nodes = asyncio.run(_rows(GraphNode, node_type="KnowledgeAsset"))
    assert {n.source_id for n in nodes} >= asset_ids
    assert all(
        n.node_metadata["category"]
        in {"Prediction", "Historical Record"}
        for n in nodes
        if n.source_id in asset_ids
    )


def test_import_skips_excluded_picks():
    """Known-bad grades (symbol collision) are never imported
    as evidence — no asset, no prediction."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NexIntel Import Entity B")
    asyncio.run(_run_import(_history(), entity["id"], user_id))
    excluded_key = pick_source_key(
        "2026-11-18T12:00:00+00:00", "LONG", "CVX", "crypto"
    )
    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    assert excluded_key not in {a.source for a in assets}
    assert not any("9457" in (a.description or "") for a in assets)


def test_duplicate_class_disambiguated():
    """Two CVX shorts in one run — crypto Convex Finance and
    equity Chevron — are two distinct picks, not one."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NexIntel Import Entity C")
    asyncio.run(_run_import(_history(), entity["id"], user_id))
    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    cvx_shorts = [
        a
        for a in assets
        if a.source.startswith("nexintel:2026-11-19")
        and ":SHORT:CVX:" in a.source
    ]
    assert len(cvx_shorts) == 2
    assert {a.source.split(":")[-1] for a in cvx_shorts} == {
        "crypto",
        "equity",
    }
    assert len({a.id for a in cvx_shorts}) == 2
    assert len({a.title for a in cvx_shorts}) == 2


def test_import_is_idempotent():
    """Re-running the import creates nothing new — same counts,
    same rows, aggregate untouched."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NexIntel Import Entity D")
    history = _history()
    first = asyncio.run(_run_import(history, entity["id"], user_id))
    assert first["knowledge_assets_created"] == 6
    second = asyncio.run(_run_import(history, entity["id"], user_id))
    assert second["knowledge_assets_created"] == 0
    assert second["predictions_created"] == 0
    assert second["outcomes_created"] == 0
    assert second["calibrations_created"] == 0
    assert second["skipped_duplicate"] == 6
    assert second["aggregate_created"] == 0
    assert second["aggregate_updated"] == 0

    assert len(asyncio.run(_rows(Prediction, entity_id=entity["id"]))) == 6
    assert len(asyncio.run(_rows(Outcome))) >= 4
    assert (
        len(asyncio.run(_rows(KnowledgeAsset, entity_id=entity["id"])))
        == 7
    )


def test_rerun_resolves_newly_graded():
    """A pick ungraded on the first run is resolved on a re-run
    with a fresher file: outcome + calibration appear, no
    duplicate prediction or asset."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NexIntel Import Entity E")
    history = _history()
    first = asyncio.run(_run_import(history, entity["id"], user_id))
    assert first["outcomes_created"] == 4

    # The SOL short (ungraded in run 1) is graded in the
    # fresher file: win, realized +15.0% against 10.0% expected.
    fresher = copy.deepcopy(history)
    sol = fresher["picks"][0]["shorts"][0]
    assert sol["asset"] == "SOL" and sol["win"] is None
    sol["win"] = True
    sol["return_pct"] = 15.0
    sol["exit"] = 170.0
    second = asyncio.run(_run_import(fresher, entity["id"], user_id))
    assert second["outcomes_created"] == 1
    assert second["calibrations_created"] == 1
    assert second["resolved_outcomes"] == 1
    assert second["resolved_calibrations"] == 1
    assert second["predictions_created"] == 0
    assert second["knowledge_assets_created"] == 0

    # CE for SOL: 15.0 - 10.0 = 5.0.
    calibrations = asyncio.run(_rows(CalibrationRecord))
    by_predicted = {c.predicted_value: c.error for c in calibrations}
    assert by_predicted[10.0] == 5.0


def test_aggregate_asset_records_stats():
    """The loop's own summary lives on one Historical Record
    asset — verbatim numbers, plus the reconciliation note."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NexIntel Import Entity F")
    asyncio.run(_run_import(_history(), entity["id"], user_id))
    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    aggregates = [
        a for a in assets if a.source == AGGREGATE_SOURCE_KEY
    ]
    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert aggregate.category == "Historical Record"
    assert "75.0" in aggregate.description
    assert "MAE 5.5" in aggregate.description
    assert "Reconciliation" in aggregate.description
