"""Tests for Engine v1 Sprint 5 — the Validation Engine.

Illustrative values used throughout are small, clearly labeled
teaching examples — not real figures.

Acceptance tests (AT-501 … AT-508) are the formal Sprint 5
acceptance suite; the remaining tests cover the seed, the Score
Registry as source of truth, the one-outcome-per-prediction
rule, server-side computation (calibration error, drift change
and status), version history, validation, ownership, and the
OpenAPI contract.

The DB-backed endpoint tests use an in-memory SQLite database
via aiosqlite (a test-only dependency — it is NOT used in
production, which runs PostgreSQL). They exercise the real auth
path: create a user, log in, then work the registry with the
JWT.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.registry import (
    CalibrationRecord,
    DriftRecord,
    DriftStatus,
    ModelStatus,
    ModelVersionHistory,
    Outcome,
    Prediction,
    RegistryModel,
    ScoreRegistry,
    ValidationLevel,
)
from app.models.vault import AuditLog
from app.services.registry_service import (
    SEED_MODELS,
    drift_status,
    seed_registry,
)


# --- Enums and models (no database) ---
def test_validation_levels():
    """The five validation levels of Framework v1.0, section
    4.1."""
    assert {level.value for level in ValidationLevel} == {
        "Conceptual",
        "Prototype",
        "Experimental",
        "Validated",
        "Institutional",
    }


def test_model_statuses():
    assert {s.value for s in ModelStatus} == {
        "active",
        "deprecated",
        "retired",
    }


def test_drift_statuses():
    assert {s.value for s in DriftStatus} == {
        "normal",
        "warning",
        "critical",
    }


def test_registry_tables():
    assert RegistryModel.__tablename__ == "registry_models"
    assert ScoreRegistry.__tablename__ == "registry_scores"
    assert Prediction.__tablename__ == "registry_predictions"
    assert Outcome.__tablename__ == "registry_outcomes"
    assert CalibrationRecord.__tablename__ == "registry_calibration"
    assert DriftRecord.__tablename__ == "registry_drift"
    assert ModelVersionHistory.__tablename__ == "model_version_history"


def test_registry_model_columns():
    for col in (
        "model_id",
        "name",
        "version",
        "description",
        "validation_level",
        "status",
        "owner",
    ):
        assert col in RegistryModel.__table__.columns
    for col in (
        "score_name",
        "current_formula",
        "current_status",
        "validation_level",
        "active_version",
    ):
        assert col in ScoreRegistry.__table__.columns
    for col in (
        "prediction_id",
        "entity_id",
        "model_id",
        "prediction_date",
        "forecast_horizon",
        "predicted_outcome",
        "confidence",
    ):
        assert col in Prediction.__table__.columns
    for col in (
        "outcome_id",
        "prediction_id",
        "actual_result",
        "recorded_date",
        "notes",
    ):
        assert col in Outcome.__table__.columns
    for col in (
        "record_id",
        "model_id",
        "observed_value",
        "predicted_value",
        "error",
        "prediction_id",
        "outcome_id",
    ):
        assert col in CalibrationRecord.__table__.columns
    for col in (
        "id",
        "model_id",
        "metric",
        "baseline",
        "current",
        "change",
        "status",
    ):
        assert col in DriftRecord.__table__.columns


def test_drift_status_thresholds():
    """normal <= 5%, warning > 5% and <= 20%, critical above
    20% (relative change)."""
    assert drift_status(100, 100) == "normal"
    assert drift_status(100, 105) == "normal"
    assert drift_status(100, 105.01) == "warning"
    assert drift_status(100, 120) == "warning"
    assert drift_status(100, 120.01) == "critical"
    assert drift_status(100, 80) == "warning"
    assert drift_status(100, 79) == "critical"
    assert drift_status(0, 0) == "normal"
    assert drift_status(0, 5) == "critical"


# --- DB-backed endpoint tests (in-memory SQLite, real auth path) ---
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
# SQLite does not enforce foreign keys by default; the registry
# relies on ON DELETE CASCADE, so enable them for the test
# database. The production database is PostgreSQL, where they are
# enforced.
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
    """Create a fresh user (unique email per call), log in,
    return (client, user_id) with the token set."""
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


def _make_entity(client: TestClient, name: str = "Illustrative Person") -> dict:
    r = client.post("/api/v1/entities", json={"type": "person", "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _make_model(client: TestClient, name: str | None = None, **extra) -> dict:
    payload = {"name": name or f"Illustrative Model {uuid.uuid4().hex[:8]}"}
    payload.update(extra)
    r = client.post("/api/v1/registry/models", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _make_prediction(client: TestClient, entity_id: int, model_id: int, outcome=100, **extra) -> dict:
    payload = {
        "entity_id": entity_id,
        "model_id": model_id,
        "forecast_horizon": "30d",
        "predicted_outcome": outcome,
        "confidence": 0.8,
    }
    payload.update(extra)
    r = client.post("/api/v1/registry/predictions", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _audit_rows(record_type: str, record_id: int | None):
    async with _test_session() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.record_type == record_type,
                AuditLog.record_id == record_id,
            )
        )
        return list(result.scalars().all())


# --- Seed ---
def test_seed_registry():
    """The nine named models + nine scores, all Experimental,
    all active; a second seed adds nothing."""
    async def _seed():
        async with _test_session() as session:
            first = await seed_registry(session)
            await session.commit()
            second = await seed_registry(session)
            await session.commit()
            return first, second

    first, second = asyncio.run(_seed())
    assert first == {"models_created": 9, "scores_created": 9}
    assert second == {"models_created": 0, "scores_created": 0}
    assert len(SEED_MODELS) == 9
    assert {m["name"] for m in SEED_MODELS} == {
        "GP$ Index",
        "Prosperity Score",
        "Trust Score",
        "Opportunity Score",
        "Hidden Asset Multiplier",
        "Stinnes Score",
        "Asset Flipper Score",
        "Anti-Fragility Index",
        "Regulatory Friction Score",
    }
    assert all(m["validation_level"] == "Experimental" for m in SEED_MODELS)
    assert all(m["formula"] for m in SEED_MODELS)


def test_seed_visible_via_api():
    """The seeded models and scores are retrievable through the
    API, all Experimental."""
    client, _ = _auth_client()
    r = client.get("/api/v1/registry/models?limit=200")
    assert r.status_code == 200, r.text
    names = {m["name"] for m in r.json()}
    assert "GP$ Index" in names
    assert "Stinnes Score" in names
    r = client.get("/api/v1/registry/scores?limit=200")
    assert r.status_code == 200, r.text
    scores = {s["score_name"]: s for s in r.json()}
    assert scores["Stinnes Score"]["validation_level"] == "Experimental"
    assert "Debt Principal" in scores["Stinnes Score"]["current_formula"]
    assert "Asset Flipper" in scores["Asset Flipper Score"]["current_formula"]


# --- AT-501 Register Model ---
def test_at501_register_model():
    """AT-501: a new model, when registered, is persisted and
    retrievable."""
    client, _ = _auth_client()
    model = _make_model(
        client,
        name="Illustrative AT-501 Model",
        description="A teaching example model.",
        owner="Illustrative Owner",
    )
    assert model["validation_level"] == "Experimental"
    assert model["status"] == "active"
    assert model["version"] == "1.0"
    r = client.get(f"/api/v1/registry/models/{model['model_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Illustrative AT-501 Model"
    assert r.json()["owner"] == "Illustrative Owner"
    r = client.get("/api/v1/registry/models?validation_level=Experimental&limit=200")
    assert r.status_code == 200, r.text
    assert any(m["model_id"] == model["model_id"] for m in r.json())


def test_duplicate_model_name_rejected():
    client, _ = _auth_client()
    _make_model(client, name="Illustrative Duplicate Model")
    r = client.post(
        "/api/v1/registry/models",
        json={"name": "Illustrative Duplicate Model"},
    )
    assert r.status_code == 409, r.text


def test_invalid_validation_level_rejected():
    client, _ = _auth_client()
    r = client.post(
        "/api/v1/registry/models",
        json={"name": "Illustrative Bad Level", "validation_level": "Proven"},
    )
    assert r.status_code == 422, r.text


# --- AT-502 Prediction Lifecycle ---
def test_at502_prediction_lifecycle():
    """AT-502: a prediction is created, an outcome is recorded,
    and they are linked correctly."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative AT-502 Model")
    pred = _make_prediction(
        client, entity["id"], model["model_id"], outcome=100
    )
    assert pred["entity_id"] == entity["id"]
    assert pred["model_id"] == model["model_id"]
    assert pred["forecast_horizon"] == "30d"
    assert pred["predicted_outcome"] == 100
    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": pred["prediction_id"], "actual_result": 115, "notes": "Observed."},
    )
    assert r.status_code == 201, r.text
    outcome = r.json()
    assert outcome["prediction_id"] == pred["prediction_id"]
    assert outcome["actual_result"] == 115
    r = client.get(f"/api/v1/registry/outcomes/{outcome['outcome_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prediction_id"] == pred["prediction_id"]
    r = client.get(
        f"/api/v1/registry/outcomes?prediction_id={pred['prediction_id']}"
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    r = client.get(
        f"/api/v1/registry/predictions?entity_id={entity['id']}"
    )
    assert r.status_code == 200, r.text
    assert any(p["prediction_id"] == pred["prediction_id"] for p in r.json())


def test_one_outcome_per_prediction():
    """A second outcome for the same prediction is rejected
    with 409, never silently overwritten."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative One Outcome Model")
    pred = _make_prediction(client, entity["id"], model["model_id"])
    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": pred["prediction_id"], "actual_result": 10},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": pred["prediction_id"], "actual_result": 20},
    )
    assert r.status_code == 409, r.text
    r = client.get(
        f"/api/v1/registry/outcomes?prediction_id={pred['prediction_id']}"
    )
    assert r.json()[0]["actual_result"] == 10


def test_prediction_requires_owned_entity_and_known_model():
    client, _ = _auth_client()
    other, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative Ownership Model")
    r = other.post(
        "/api/v1/registry/predictions",
        json={
            "entity_id": entity["id"],
            "model_id": model["model_id"],
            "forecast_horizon": "30d",
            "predicted_outcome": 1,
            "confidence": 0.5,
        },
    )
    assert r.status_code == 403, r.text
    r = client.post(
        "/api/v1/registry/predictions",
        json={
            "entity_id": entity["id"],
            "model_id": 999999,
            "forecast_horizon": "30d",
            "predicted_outcome": 1,
            "confidence": 0.5,
        },
    )
    assert r.status_code == 404, r.text
    r = client.post(
        "/api/v1/registry/predictions",
        json={
            "entity_id": entity["id"],
            "model_id": model["model_id"],
            "forecast_horizon": "30d",
            "predicted_outcome": 1,
            "confidence": 1.5,
        },
    )
    assert r.status_code == 422, r.text


# --- AT-503 Calibration Record ---
def test_at503_calibration_from_prediction():
    """AT-503: given a prediction + outcome, a calibration
    error is generated — CE = Observed - Predicted, computed
    server-side."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative AT-503 Model")
    pred = _make_prediction(client, entity["id"], model["model_id"], outcome=100)
    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": pred["prediction_id"], "actual_result": 115},
    )
    assert r.status_code == 201, r.text
    outcome = r.json()
    r = client.post(
        "/api/v1/registry/calibration/from-prediction",
        json={
            "prediction_id": pred["prediction_id"],
            "outcome_id": outcome["outcome_id"],
        },
    )
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["model_id"] == model["model_id"]
    assert record["observed_value"] == 115
    assert record["predicted_value"] == 100
    assert record["error"] == 15
    assert record["prediction_id"] == pred["prediction_id"]
    assert record["outcome_id"] == outcome["outcome_id"]
    r = client.get(f"/api/v1/registry/calibration/{record['record_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["error"] == 15


def test_calibration_from_prediction_object_values():
    """An object outcome with a numeric "value" key calibrates;
    a non-numeric value is refused (422)."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative AT-503b Model")
    pred = _make_prediction(
        client, entity["id"], model["model_id"], outcome={"value": 50}
    )
    r = client.post(
        "/api/v1/registry/outcomes",
        json={
            "prediction_id": pred["prediction_id"],
            "actual_result": {"value": 55},
        },
    )
    outcome = r.json()
    r = client.post(
        "/api/v1/registry/calibration/from-prediction",
        json={
            "prediction_id": pred["prediction_id"],
            "outcome_id": outcome["outcome_id"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["error"] == 5

    pred2 = _make_prediction(
        client, entity["id"], model["model_id"], outcome={"note": "up"}
    )
    r = client.post(
        "/api/v1/registry/outcomes",
        json={
            "prediction_id": pred2["prediction_id"],
            "actual_result": {"note": "down"},
        },
    )
    outcome2 = r.json()
    r = client.post(
        "/api/v1/registry/calibration/from-prediction",
        json={
            "prediction_id": pred2["prediction_id"],
            "outcome_id": outcome2["outcome_id"],
        },
    )
    assert r.status_code == 422, r.text


def test_calibration_from_prediction_mismatched_outcome():
    """An outcome that does not belong to the prediction is
    refused (422)."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative AT-503c Model")
    pred_a = _make_prediction(client, entity["id"], model["model_id"], outcome=1)
    pred_b = _make_prediction(client, entity["id"], model["model_id"], outcome=2)
    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": pred_a["prediction_id"], "actual_result": 1},
    )
    outcome_a = r.json()
    r = client.post(
        "/api/v1/registry/calibration/from-prediction",
        json={
            "prediction_id": pred_b["prediction_id"],
            "outcome_id": outcome_a["outcome_id"],
        },
    )
    assert r.status_code == 422, r.text


def test_calibration_error_computed_server_side():
    """A client-supplied error is ignored: the stored error is
    always Observed - Predicted."""
    client, _ = _auth_client()
    model = _make_model(client, name="Illustrative Server Side Model")
    r = client.post(
        "/api/v1/registry/calibration",
        json={
            "model_id": model["model_id"],
            "observed_value": 10,
            "predicted_value": 4,
            "error": 999,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["error"] == 6
    r = client.get(
        f"/api/v1/registry/calibration?model_id={model['model_id']}"
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["error"] == 6


# --- AT-504 Validation Level Update ---
def test_at504_validation_level_update():
    """AT-504: a model exists, its validation level changes,
    the registry is updated."""
    client, _ = _auth_client()
    model = _make_model(client, name="Illustrative AT-504 Model")
    assert model["validation_level"] == "Experimental"
    r = client.patch(
        f"/api/v1/registry/models/{model['model_id']}",
        json={"validation_level": "Validated"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["validation_level"] == "Validated"
    assert r.json()["version"] == "1.0"
    r = client.get(f"/api/v1/registry/models/{model['model_id']}")
    assert r.json()["validation_level"] == "Validated"
    r = client.get("/api/v1/registry/models?validation_level=Validated")
    assert any(m["model_id"] == model["model_id"] for m in r.json())


# --- AT-505 Registry Lineage ---
def test_at505_registry_lineage():
    """AT-505: a registry record has an audit trail."""
    client, _ = _auth_client()
    model = _make_model(client, name="Illustrative AT-505 Model")
    rows = asyncio.run(_audit_rows("RegistryModel", model["model_id"]))
    assert any(e.event_type == "model_registered" for e in rows)
    assert rows[0].actor is not None

    entity = _make_entity(client)
    pred = _make_prediction(client, entity["id"], model["model_id"])
    rows = asyncio.run(_audit_rows("Prediction", pred["prediction_id"]))
    assert any(e.event_type == "prediction_created" for e in rows)

    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": pred["prediction_id"], "actual_result": 7},
    )
    outcome = r.json()
    rows = asyncio.run(_audit_rows("Outcome", outcome["outcome_id"]))
    assert any(e.event_type == "outcome_recorded" for e in rows)

    r = client.post(
        "/api/v1/registry/calibration/from-prediction",
        json={
            "prediction_id": pred["prediction_id"],
            "outcome_id": outcome["outcome_id"],
        },
    )
    record = r.json()
    rows = asyncio.run(
        _audit_rows("CalibrationRecord", record["record_id"])
    )
    assert any(e.event_type == "calibration_recorded" for e in rows)


# --- AT-506 Model Version Tracking ---
def test_at506_model_version_tracking():
    """AT-506: a version is updated and the previous version is
    preserved and retrievable."""
    client, _ = _auth_client()
    model = _make_model(
        client,
        name="Illustrative AT-506 Model",
        version="1.0",
        description="First version.",
    )
    r = client.get(
        f"/api/v1/registry/models/{model['model_id']}/versions"
    )
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = client.patch(
        f"/api/v1/registry/models/{model['model_id']}",
        json={"version": "2.0", "description": "Second version."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "2.0"
    r = client.get(
        f"/api/v1/registry/models/{model['model_id']}/versions"
    )
    versions = r.json()
    assert len(versions) == 1
    assert versions[0]["version"] == "1.0"
    assert versions[0]["description"] == "First version."
    assert versions[0]["validation_level"] == "Experimental"

    r = client.patch(
        f"/api/v1/registry/models/{model['model_id']}",
        json={"version": "3.0"},
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/v1/registry/models/{model['model_id']}/versions"
    )
    versions = r.json()
    assert [v["version"] for v in versions] == ["2.0", "1.0"]

    # A PATCH that does not change the version adds no history.
    r = client.patch(
        f"/api/v1/registry/models/{model['model_id']}",
        json={"description": "Still version 3.0."},
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/v1/registry/models/{model['model_id']}/versions"
    )
    assert len(r.json()) == 2


# --- AT-507 Drift Record Creation ---
def test_at507_drift_record_creation():
    """AT-507: metric drift detected → a drift record is
    stored, with change computed server-side."""
    client, _ = _auth_client()
    model = _make_model(client, name="Illustrative AT-507 Model")
    r = client.post(
        "/api/v1/registry/drift",
        json={
            "model_id": model["model_id"],
            "metric": "stinnes_score_mean",
            "baseline": 100,
            "current": 130,
        },
    )
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["change"] == 30
    assert record["status"] == "critical"
    r = client.get(f"/api/v1/registry/drift/{record['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "critical"

    # The threshold boundaries, through the API.
    for baseline, current, expected in (
        (100, 103, "normal"),
        (100, 110, "warning"),
        (0, 0, "normal"),
        (0, 5, "critical"),
    ):
        r = client.post(
            "/api/v1/registry/drift",
            json={
                "model_id": model["model_id"],
                "metric": "boundary_check",
                "baseline": baseline,
                "current": current,
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == expected, (baseline, current)

    r = client.get(
        f"/api/v1/registry/drift?model_id={model['model_id']}&status=critical"
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2


# --- AT-508 OpenAPI Validation ---
def test_at508_openapi_validation():
    """AT-508: with the application running, the registry
    endpoints are present in the OpenAPI schema."""
    client = TestClient(app)
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    paths = r.json()["paths"]
    for path in (
        "/api/v1/registry/models",
        "/api/v1/registry/models/{model_id}",
        "/api/v1/registry/models/{model_id}/versions",
        "/api/v1/registry/scores",
        "/api/v1/registry/scores/{score_name}",
        "/api/v1/registry/predictions",
        "/api/v1/registry/predictions/{prediction_id}",
        "/api/v1/registry/outcomes",
        "/api/v1/registry/outcomes/{outcome_id}",
        "/api/v1/registry/calibration",
        "/api/v1/registry/calibration/from-prediction",
        "/api/v1/registry/calibration/{record_id}",
        "/api/v1/registry/drift",
        "/api/v1/registry/drift/{record_id}",
    ):
        assert path in paths, path


# --- Score Registry CRUD ---
def test_score_crud():
    """The Score Registry is the source of truth: its formula,
    version, and status are the current ones."""
    client, _ = _auth_client()
    r = client.post(
        "/api/v1/registry/scores",
        json={
            "score_name": "Illustrative Score",
            "current_formula": "S = A / B",
            "active_version": "1.0",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["validation_level"] == "Experimental"
    r = client.get("/api/v1/registry/scores/Illustrative Score")
    assert r.status_code == 200, r.text
    assert r.json()["current_formula"] == "S = A / B"
    r = client.patch(
        "/api/v1/registry/scores/Illustrative Score",
        json={"current_formula": "S = A / B + C", "active_version": "2.0"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_formula"] == "S = A / B + C"
    assert r.json()["active_version"] == "2.0"
    r = client.post(
        "/api/v1/registry/scores",
        json={
            "score_name": "Illustrative Score",
            "current_formula": "S = A / B",
        },
    )
    assert r.status_code == 409, r.text
    r = client.delete("/api/v1/registry/scores/Illustrative Score")
    assert r.status_code == 204, r.text
    r = client.get("/api/v1/registry/scores/Illustrative Score")
    assert r.status_code == 404, r.text


# --- CRUD + ownership + auth ---
def test_model_and_prediction_crud():
    client, _ = _auth_client()
    model = _make_model(client, name="Illustrative CRUD Model")
    entity = _make_entity(client)
    pred = _make_prediction(client, entity["id"], model["model_id"])
    r = client.patch(
        f"/api/v1/registry/predictions/{pred['prediction_id']}",
        json={"confidence": 0.9},
    )
    assert r.status_code == 200, r.text
    assert r.json()["confidence"] == 0.9
    r = client.delete(
        f"/api/v1/registry/predictions/{pred['prediction_id']}"
    )
    assert r.status_code == 204, r.text
    r = client.get(
        f"/api/v1/registry/predictions/{pred['prediction_id']}"
    )
    assert r.status_code == 404, r.text
    r = client.delete(f"/api/v1/registry/models/{model['model_id']}")
    assert r.status_code == 204, r.text
    r = client.get(f"/api/v1/registry/models/{model['model_id']}")
    assert r.status_code == 404, r.text


def test_prediction_read_requires_access():
    client, _ = _auth_client()
    other, _ = _auth_client()
    entity = _make_entity(client)
    model = _make_model(client, name="Illustrative Read Access Model")
    pred = _make_prediction(client, entity["id"], model["model_id"])
    r = other.get(
        f"/api/v1/registry/predictions/{pred['prediction_id']}"
    )
    assert r.status_code == 403, r.text
    r = other.patch(
        f"/api/v1/registry/predictions/{pred['prediction_id']}",
        json={"confidence": 0.1},
    )
    assert r.status_code == 403, r.text


def test_registry_requires_auth():
    client = TestClient(app)
    r = client.get("/api/v1/registry/models")
    assert r.status_code == 401, r.text
    r = client.post(
        "/api/v1/registry/models", json={"name": "No Auth Model"}
    )
    assert r.status_code == 401, r.text
