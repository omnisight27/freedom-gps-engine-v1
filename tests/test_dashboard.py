"""Tests for Engine v1 Sprint 6 — the Sovereign Dashboard.

Illustrative values used throughout are small, clearly labeled
teaching examples — not real figures.

Acceptance tests (AT-601 … AT-605, AT-607, AT-608) are the
formal Sprint 6 acceptance suite for the dashboard; the
remaining tests cover the ledger trends, the opportunity
grouping, vault search/filter, the HTML page, ownership, and
the OpenAPI contract.

The DB-backed endpoint tests use an in-memory SQLite database
via aiosqlite (a test-only dependency — it is NOT used in
production, which runs PostgreSQL). They exercise the real auth
path: create a user, log in, then work the dashboard with the
JWT.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.registry_service import seed_registry


# --- DB-backed endpoint tests (in-memory SQLite, real auth path) ---
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
# SQLite does not enforce foreign keys by default; the vault
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
    """Create a fresh user (unique email per call), log in, return
    (client, user_id) with the token set."""
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


def _make_asset(client: TestClient, entity_id: int, title: str, category: str) -> dict:
    r = client.post(
        "/api/v1/knowledge",
        json={"entity_id": entity_id, "title": title, "category": category},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _attach_evidence(client: TestClient, asset_id: int, source: str) -> dict:
    r = client.post(
        f"/api/v1/knowledge/{asset_id}/evidence",
        json={"source": source, "source_type": "Document", "confidence": 0.9},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def dashboard_data():
    """One rich entity: two state vector snapshots, vault
    records with and without evidence, opportunities in every
    status, and a seeded registry with a prediction, an outcome,
    a calibration record, and a drift record."""
    client, _user_id = _auth_client()
    entity = _make_entity(client)

    # The Validation Engine seed: nine models, all Experimental.
    async def _seed():
        async with _test_session() as session:
            await seed_registry(session)
            await session.commit()

    asyncio.run(_seed())

    # Two state vector snapshots: financial rises, human flat,
    # social unmeasured then measured.
    r = client.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json={"financial": 100.0, "human": 50.0, "social": None},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json={"financial": 120.0, "human": 50.0, "social": 30.0},
    )
    assert r.status_code == 201, r.text

    # Knowledge assets: a document with evidence, a skill asset
    # with evidence, a certification asset with evidence.
    will = _make_asset(client, entity["id"], "Illustrative Will", "Document")
    will_evidence = _attach_evidence(client, will["id"], "Illustrative will document")
    skill_asset = _make_asset(client, entity["id"], "Illustrative Skill Asset", "Skill")
    _attach_evidence(client, skill_asset["id"], "Illustrative skill certificate")
    cert_asset = _make_asset(client, entity["id"], "Illustrative Certification Asset", "Certification")
    _attach_evidence(client, cert_asset["id"], "Illustrative certification document")

    # Skills: one with no evidence, one evidenced through its asset.
    r = client.post(
        "/api/v1/skills",
        json={"entity_id": entity["id"], "name": "Illustrative Unevidenced Skill"},
    )
    assert r.status_code == 201, r.text
    unevidenced_skill = r.json()
    r = client.post(
        "/api/v1/skills",
        json={
            "entity_id": entity["id"],
            "name": "Illustrative Evidenced Skill",
            "knowledge_asset_id": skill_asset["id"],
        },
    )
    assert r.status_code == 201, r.text

    # Certifications: one with no evidence, one evidenced.
    r = client.post(
        "/api/v1/certifications",
        json={"entity_id": entity["id"], "title": "Illustrative Unevidenced Certification"},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/certifications",
        json={
            "entity_id": entity["id"],
            "title": "Illustrative Evidenced Certification",
            "knowledge_asset_id": cert_asset["id"],
        },
    )
    assert r.status_code == 201, r.text

    # Family record and legacy assets (one preserved, one not).
    r = client.post(
        "/api/v1/family-records",
        json={"entity_id": entity["id"], "title": "Illustrative Family Letter", "record_type": "Letter"},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/legacy-assets",
        json={
            "entity_id": entity["id"],
            "title": "Illustrative Preserved Book",
            "asset_type": "Book",
            "preservation_status": "Preserved",
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/legacy-assets",
        json={"entity_id": entity["id"], "title": "Illustrative Unpreserved Book", "asset_type": "Book"},
    )
    assert r.status_code == 201, r.text

    # Opportunities in every status: two open, one dismissed,
    # one resolved. The detected one links to the unevidenced
    # skill and to the will's evidence.
    def _make_opp(title, status, detector, source_type, source_id, evidence_id=None, description=None):
        r = client.post(
            "/api/v1/opportunities",
            json={
                "entity_id": entity["id"],
                "title": title,
                "description": description,
                "opportunity_type": "Skill Opportunity",
                "confidence": 0.9,
                "status": status,
                "detector": detector,
                "source_type": source_type,
                "source_id": source_id,
                "evidence_id": evidence_id,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    opp_open = _make_opp(
        "Skill with no evidence: Illustrative Unevidenced Skill",
        "detected",
        "skills_no_evidence",
        "Skill",
        unevidenced_skill["id"],
        evidence_id=will_evidence["id"],
        description=(
            "The skill 'Illustrative Unevidenced Skill' has no "
            "evidence. Add evidence — a certificate, a project, a "
            "transcript — to make this capability verifiable."
        ),
    )
    _make_opp(
        "Second open opportunity",
        "accepted",
        "manual",
        None,
        None,
    )
    _make_opp(
        "Dismissed opportunity",
        "dismissed",
        "manual",
        None,
        None,
    )
    _make_opp(
        "Resolved opportunity",
        "realized",
        "manual",
        None,
        None,
    )

    # Validation data: one prediction, its outcome, a
    # calibration record generated from the pair, one drift
    # record.
    r = client.get("/api/v1/registry/models?validation_level=Experimental")
    assert r.status_code == 200, r.text
    model_id = r.json()[0]["model_id"]
    r = client.post(
        "/api/v1/registry/predictions",
        json={
            "entity_id": entity["id"],
            "model_id": model_id,
            "forecast_horizon": "30d",
            "predicted_outcome": 42,
            "confidence": 0.8,
        },
    )
    assert r.status_code == 201, r.text
    prediction = r.json()
    r = client.post(
        "/api/v1/registry/outcomes",
        json={"prediction_id": prediction["prediction_id"], "actual_result": 45},
    )
    assert r.status_code == 201, r.text
    outcome = r.json()
    r = client.post(
        "/api/v1/registry/calibration/from-prediction",
        json={"prediction_id": prediction["prediction_id"], "outcome_id": outcome["outcome_id"]},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/registry/drift",
        json={"model_id": model_id, "metric": "illustrative metric", "baseline": 100, "current": 120},
    )
    assert r.status_code == 201, r.text

    return {
        "client": client,
        "entity": entity,
        "will": will,
        "will_evidence": will_evidence,
        "unevidenced_skill": unevidenced_skill,
        "opp_open": opp_open,
        "model_id": model_id,
    }


# --- AT-601: Dashboard Loads ---
def test_at601_dashboard_loads(dashboard_data):
    """An authenticated user can load the dashboard: the HTML
    page contains all six module sections, and the executive
    overview endpoint returns the summary."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]

    r = client.get(f"/dashboard?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    for section in (
        "Executive Overview",
        "Complete Ledger",
        "State Vector Explorer",
        "Opportunity Center",
        "Knowledge Vault",
        "Validation Center",
    ):
        assert section in r.text, f"missing section: {section}"

    r = client.get(f"/api/v1/dashboard?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity"]["id"] == entity_id
    assert body["state_vector"] is not None
    assert body["opportunity_count"] == 2
    assert body["knowledge_assets_count"] == 3
    assert body["legacy_assets_count"] == 2
    assert body["skills_count"] == 2
    assert body["certifications_count"] == 2
    assert body["validation_status"]["Experimental"] == 9


def test_at601_dashboard_page_without_entity_id(dashboard_data):
    """With no entity_id, the page shows the caller's first
    accessible entity."""
    client = dashboard_data["client"]
    r = client.get("/dashboard")
    assert r.status_code == 200, r.text
    assert "Executive Overview" in r.text


def test_dashboard_requires_auth(dashboard_data):
    """Unauthenticated requests are rejected."""
    client = TestClient(app)
    entity_id = dashboard_data["entity"]["id"]
    assert client.get(f"/api/v1/dashboard?entity_id={entity_id}").status_code == 401
    assert client.get(f"/dashboard?entity_id={entity_id}").status_code == 401


def test_dashboard_unknown_entity_404(dashboard_data):
    client = dashboard_data["client"]
    r = client.get("/api/v1/dashboard?entity_id=999999")
    assert r.status_code == 404, r.text


def test_dashboard_other_users_entity_403(dashboard_data):
    """An entity owned by someone else is not readable."""
    other_client, _ = _auth_client()
    entity_id = dashboard_data["entity"]["id"]
    r = other_client.get(f"/api/v1/dashboard?entity_id={entity_id}")
    assert r.status_code == 403, r.text


# --- Module 2: Complete Ledger ---
def test_ledger_trends(dashboard_data):
    """Financial rose (up), human flat, social unmeasured then
    measured (flat, no delta). Trend indicators only."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/ledger?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity_id
    assert [c["key"] for c in body["capitals"]] == ["F", "H", "S", "R", "L"]
    by_key = {c["key"]: c for c in body["capitals"]}
    assert by_key["F"]["value"] == 120.0
    assert by_key["F"]["previous_value"] == 100.0
    assert by_key["F"]["delta"] == 20.0
    assert by_key["F"]["trend"] == "up"
    assert by_key["H"]["value"] == 50.0
    assert by_key["H"]["delta"] == 0.0
    assert by_key["H"]["trend"] == "flat"
    assert by_key["S"]["value"] == 30.0
    assert by_key["S"]["previous_value"] is None
    assert by_key["S"]["delta"] is None
    assert by_key["S"]["trend"] == "flat"
    assert body["as_of"] is not None


# --- AT-602: State Vector Display ---
def test_at602_state_vector_display(dashboard_data):
    """An entity exists → Ω(t) is displayed: all ten
    components, latest changes, and history."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/state-vector?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity_id
    current = body["current"]
    assert current is not None
    for component in (
        "financial", "human", "social", "resource", "legacy",
        "trust", "memory", "intelligence", "entropy", "opportunity",
    ):
        assert component in current, f"missing component: {component}"
    assert current["financial"] == 120.0
    assert len(body["history"]) == 2
    changes = {c["component"]: c for c in body["changes"]}
    assert changes["F"]["previous"] == 100.0
    assert changes["F"]["current"] == 120.0
    assert changes["F"]["delta"] == 20.0
    # Human did not move, so it is not a change. Social was
    # unmeasured in the previous snapshot, so no change can be
    # computed for it either.
    assert "H" not in changes
    assert "S" not in changes


# --- AT-603: Opportunity Feed ---
def test_at603_opportunity_feed(dashboard_data):
    """Open opportunities exist → they are displayed with
    source, confidence, evidence, and reason."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/opportunities?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity_id
    assert body["open_count"] == 2
    assert body["dismissed_count"] == 1
    assert body["resolved_count"] == 1
    open_opp = next(
        o for o in body["open"] if o["id"] == dashboard_data["opp_open"]["id"]
    )
    assert open_opp["detector"] == "skills_no_evidence"
    assert open_opp["source_type"] == "Skill"
    assert open_opp["source_id"] == dashboard_data["unevidenced_skill"]["id"]
    assert open_opp["confidence"] == 0.9
    assert "no evidence" in open_opp["description"]
    assert open_opp["evidence"] is not None
    assert open_opp["evidence"]["id"] == dashboard_data["will_evidence"]["id"]


# --- AT-604: Knowledge Vault View ---
def test_at604_knowledge_vault_view(dashboard_data):
    """Knowledge assets exist → they are visible; search and
    filter work."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/vault?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity_id
    assert body["knowledge_assets_count"] == 3
    assert body["skills_count"] == 2
    assert body["certifications_count"] == 2
    assert body["family_records_count"] == 1
    assert body["legacy_assets_count"] == 2
    assert body["evidence_count"] == 3
    titles = {a["title"] for a in body["knowledge_assets"]}
    assert "Illustrative Will" in titles

    # Search: ?q= matches title.
    r = client.get(f"/api/v1/dashboard/vault?entity_id={entity_id}&q=Will")
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_assets_count"] == 1
    assert r.json()["knowledge_assets"][0]["title"] == "Illustrative Will"

    # Filter: ?category= narrows the assets.
    r = client.get(f"/api/v1/dashboard/vault?entity_id={entity_id}&category=Skill")
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_assets_count"] == 1
    assert r.json()["knowledge_assets"][0]["category"] == "Skill"


# --- AT-605: Validation Center ---
def test_at605_validation_center(dashboard_data):
    """Models exist → validation data is displayed: models,
    validation levels, predictions, outcomes, calibration
    errors, drift records."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/validation?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity_id
    assert len(body["models"]) == 9
    assert body["validation_levels"]["Experimental"] == 9
    assert len(body["scores"]) == 9
    assert len(body["predictions"]) == 1
    assert body["predictions"][0]["predicted_outcome"] == 42
    assert len(body["outcomes"]) == 1
    assert body["outcomes"][0]["actual_result"] == 45
    assert len(body["calibration_records"]) == 1
    assert body["calibration_records"][0]["error"] == 3.0
    assert len(body["drift_records"]) == 1
    assert body["drift_records"][0]["status"] == "warning"


# --- AT-607: Evidence Traceability ---
def test_at607_evidence_traceability(dashboard_data):
    """A knowledge record → its evidence is visible, in the
    vault and on the opportunity that links to it."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/vault?entity_id={entity_id}")
    body = r.json()
    will_evidence = [
        e
        for e in body["evidence"]
        if e["knowledge_asset_id"] == dashboard_data["will"]["id"]
    ]
    assert len(will_evidence) == 1
    assert will_evidence[0]["source"] == "Illustrative will document"
    assert will_evidence[0]["confidence"] == 0.9
    # The asset carries its lineage.
    will = next(
        a for a in body["knowledge_assets"] if a["id"] == dashboard_data["will"]["id"]
    )
    assert will["version"] == 1
    assert will["source"] is None


# --- AT-608: Audit Visibility ---
def test_at608_audit_visibility(dashboard_data):
    """Audit records exist → the audit history is displayed."""
    client = dashboard_data["client"]
    entity_id = dashboard_data["entity"]["id"]
    r = client.get(f"/api/v1/dashboard/audit?entity_id={entity_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity_id
    assert len(body["entries"]) > 0
    event_types = {e["event_type"] for e in body["entries"]}
    assert "knowledge_created" in event_types
    assert "opportunity_created" in event_types
    # Newest first.
    timestamps = [e["timestamp"] for e in body["entries"]]
    assert timestamps == sorted(timestamps, reverse=True)


# --- OpenAPI contract ---
def test_dashboard_endpoints_in_openapi():
    """All dashboard + assistant endpoints are present in the
    OpenAPI schema (AT-508 pattern)."""
    paths = set(app.openapi()["paths"].keys())
    for path in (
        "/api/v1/dashboard",
        "/api/v1/dashboard/ledger",
        "/api/v1/dashboard/state-vector",
        "/api/v1/dashboard/opportunities",
        "/api/v1/dashboard/vault",
        "/api/v1/dashboard/validation",
        "/api/v1/dashboard/audit",
        "/dashboard",
        "/api/v1/assistant/ask",
    ):
        assert path in paths, f"missing from OpenAPI: {path}"
