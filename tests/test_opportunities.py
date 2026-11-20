"""Tests for Engine v1 Sprint 4 — the Opportunity Engine.

Illustrative values used throughout are small, clearly labeled
teaching examples — not real figures.

Acceptance tests (AT-401 … AT-408) are the formal Sprint 4
acceptance suite; the remaining tests cover the six detectors, the
detector -> type mapping, idempotency, auto-dismiss on gap close,
validation, ownership, and the OpenAPI contract.

The DB-backed endpoint tests use an in-memory SQLite database via
aiosqlite (a test-only dependency — it is NOT used in production,
which runs PostgreSQL). They exercise the real auth path: create a
user, log in, then work the opportunities with the JWT.
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
from app.models.opportunity import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
)


# --- Enums and models (no database) ---
def test_opportunity_types():
    """The five opportunity types of Sprint 4."""
    assert {t.value for t in OpportunityType} == {
        "Skill Opportunity", "Asset Opportunity",
        "Knowledge Opportunity", "Relationship Opportunity",
        "Legacy Opportunity",
    }


def test_opportunity_statuses():
    assert {t.value for t in OpportunityStatus} == {
        "detected", "accepted", "dismissed", "realized",
    }


def test_opportunity_model_table():
    assert Opportunity.__tablename__ == "opportunities"
    for col in (
        "entity_id", "title", "description", "opportunity_type",
        "confidence", "status", "detector", "source_type",
        "source_id", "evidence_id",
    ):
        assert col in Opportunity.__table__.columns


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


def _make_entity(client: TestClient, type_: str = "person", name: str = "Illustrative Person") -> dict:
    r = client.post("/api/v1/entities", json={"type": type_, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _make_asset(client: TestClient, entity_id: int, title: str = "Illustrative Asset", category: str = "Document", **extra) -> dict:
    payload = {"entity_id": entity_id, "title": title, "category": category}
    payload.update(extra)
    r = client.post("/api/v1/knowledge", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _make_skill(client: TestClient, entity_id: int, name: str = "Illustrative Skill", **extra) -> dict:
    payload = {"entity_id": entity_id, "name": name}
    payload.update(extra)
    r = client.post("/api/v1/skills", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _make_certification(client: TestClient, entity_id: int, title: str = "Illustrative Certificate", **extra) -> dict:
    payload = {"entity_id": entity_id, "title": title}
    payload.update(extra)
    r = client.post("/api/v1/certifications", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _make_legacy_asset(client: TestClient, entity_id: int, title: str = "Illustrative Book", **extra) -> dict:
    payload = {"entity_id": entity_id, "title": title, "asset_type": "Book"}
    payload.update(extra)
    r = client.post("/api/v1/legacy-assets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _attach_evidence(client: TestClient, asset_id: int, source: str = "illustrative source", source_type: str = "Document", confidence: float = 0.9) -> dict:
    r = client.post(
        f"/api/v1/knowledge/{asset_id}/evidence",
        json={"source": source, "source_type": source_type, "confidence": confidence},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _detect(client: TestClient, entity_id: int) -> dict:
    r = client.post("/api/v1/opportunities/detect", json={"entity_id": entity_id})
    assert r.status_code == 200, r.text
    return r.json()


def _list_opportunities(client: TestClient, **params) -> list:
    r = client.get("/api/v1/opportunities", params={**params, "limit": 200})
    assert r.status_code == 200, r.text
    return r.json()


def _audit_for(client: TestClient, record_id: int, event_type: str | None = None) -> list:
    params = {"record_type": "Opportunity", "record_id": record_id, "limit": 200}
    if event_type is not None:
        params["event_type"] = event_type
    r = client.get("/api/v1/vault/audit-log", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _node_for(client: TestClient, node_type: str, source_id: int) -> dict:
    r = client.get(
        "/api/v1/graph/nodes",
        params={"node_type": node_type, "limit": 200},
    )
    assert r.status_code == 200, r.text
    for node in r.json():
        if node["source_id"] == source_id:
            return node
    raise AssertionError(f"no {node_type} node for source {source_id}")


def _edges_from(client: TestClient, node_id: int, relationship_type: str | None = None) -> list:
    params = {"from_node_id": node_id, "limit": 200}
    if relationship_type is not None:
        params["relationship_type"] = relationship_type
    r = client.get("/api/v1/graph/relationships", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- AT-401 Hidden Skill Detection ---
def test_at401_hidden_skill_detection():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Unproven Skill")

    result = _detect(client, entity["id"])
    created = result["created"]
    assert result["created_count"] == 1
    opp = created[0]
    assert opp["opportunity_type"] == "Skill Opportunity"
    assert opp["detector"] == "skills_no_evidence"
    assert opp["source_type"] == "Skill"
    assert opp["source_id"] == skill["id"]
    assert opp["status"] == "detected"
    assert 0 <= opp["confidence"] <= 1


# --- AT-402 Knowledge Gap Detection ---
def test_at402_knowledge_gap_detection():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Lonely Document", "Document")

    result = _detect(client, entity["id"])
    assert result["created_count"] == 1
    opp = result["created"][0]
    assert opp["opportunity_type"] == "Knowledge Opportunity"
    assert opp["detector"] == "knowledge_assets_no_relationships"
    assert opp["source_type"] == "KnowledgeAsset"
    assert opp["source_id"] == asset["id"]


# --- AT-403 Legacy Preservation Detection ---
def test_at403_legacy_preservation_detection():
    client, _ = _auth_client()
    entity = _make_entity(client)
    legacy = _make_legacy_asset(client, entity["id"], "Unprotected Book")

    result = _detect(client, entity["id"])
    assert result["created_count"] == 1
    opp = result["created"][0]
    assert opp["opportunity_type"] == "Legacy Opportunity"
    assert opp["detector"] == "legacy_no_preservation_status"
    assert opp["source_type"] == "LegacyAsset"
    assert opp["source_id"] == legacy["id"]


# --- AT-404 Opportunity CRUD ---
def test_at404_opportunity_crud():
    client, _ = _auth_client()
    entity = _make_entity(client)

    r = client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": entity["id"],
            "title": "Hand-made opportunity",
            "description": "Created by hand for the test.",
            "opportunity_type": "Relationship Opportunity",
            "confidence": 0.6,
        },
    )
    assert r.status_code == 201, r.text
    opp = r.json()
    assert opp["status"] == "detected"
    assert opp["detector"] == "manual"

    r = client.get(f"/api/v1/opportunities/{opp['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "Hand-made opportunity"

    r = client.patch(
        f"/api/v1/opportunities/{opp['id']}",
        json={"status": "accepted", "title": "Accepted opportunity"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["title"] == "Accepted opportunity"

    r = client.get(
        f"/api/v1/entities/{entity['id']}/opportunities"
    )
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.delete(f"/api/v1/opportunities/{opp['id']}")
    assert r.status_code == 204, r.text
    r = client.get(f"/api/v1/opportunities/{opp['id']}")
    assert r.status_code == 404


# --- AT-405 Opportunity Lineage ---
def test_at405_opportunity_lineage():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Lineage Skill")

    result = _detect(client, entity["id"])
    opp = result["created"][0]

    r = client.get(f"/api/v1/opportunities/{opp['id']}")
    assert r.status_code == 200, r.text
    found = r.json()
    # Lineage: which detector, which source record, when.
    assert found["detector"] == "skills_no_evidence"
    assert found["source_type"] == "Skill"
    assert found["source_id"] == skill["id"]
    assert found["entity_id"] == entity["id"]
    assert found["created_at"]
    assert found["updated_at"]


# --- AT-406 Opportunity Confidence ---
def test_at406_opportunity_confidence():
    client, _ = _auth_client()
    entity = _make_entity(client)
    base = {
        "entity_id": entity["id"],
        "title": "Confidence check",
        "opportunity_type": "Skill Opportunity",
    }
    # Outside 0–1 is rejected.
    for bad in (-0.1, 1.5):
        r = client.post(
            "/api/v1/opportunities",
            json={**base, "confidence": bad},
        )
        assert r.status_code == 422, r.text
    # The boundaries are accepted.
    for good in (0, 1, 0.5):
        r = client.post(
            "/api/v1/opportunities",
            json={**base, "confidence": good},
        )
        assert r.status_code == 201, r.text
    # A PATCH outside the range is rejected too.
    opp = _list_opportunities(client, entity_id=entity["id"])[0]
    r = client.patch(
        f"/api/v1/opportunities/{opp['id']}",
        json={"confidence": 2.0},
    )
    assert r.status_code == 422, r.text


# --- AT-407 Opportunity Audit ---
def test_at407_opportunity_audit():
    client, _ = _auth_client()
    entity = _make_entity(client)

    r = client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": entity["id"],
            "title": "Audited opportunity",
            "opportunity_type": "Knowledge Opportunity",
            "confidence": 0.8,
        },
    )
    assert r.status_code == 201, r.text
    opp = r.json()

    created = _audit_for(client, opp["id"], "opportunity_created")
    assert len(created) == 1
    assert created[0]["record_type"] == "Opportunity"

    r = client.patch(
        f"/api/v1/opportunities/{opp['id']}",
        json={"status": "accepted"},
    )
    assert r.status_code == 200, r.text
    changed = _audit_for(
        client, opp["id"], "opportunity_status_changed"
    )
    assert len(changed) == 1
    assert changed[0]["details"] is not None
    assert "accepted" in changed[0]["details"]


# --- AT-408 Graph Gap Query ---
def test_at408_graph_gap_query():
    """A disconnected cluster — nodes connected to each other but
    not to any entity — is found by the graph gap query (D6)."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Cluster Skill")
    asset = _make_asset(client, entity["id"], "Cluster Asset", "Document")
    entity_node = _node_for(client, "Entity", entity["id"])
    skill_node = _node_for(client, "Skill", skill["id"])
    asset_node = _node_for(client, "KnowledgeAsset", asset["id"])

    # Cut every edge from the entity node (OWNS and the typed
    # HAS_SKILL edge) so the skill and asset no longer reach the
    # entity, then link them to each other.
    for edge in _edges_from(client, entity_node["id"]):
        r = client.delete(f"/api/v1/graph/relationships/{edge['id']}")
        assert r.status_code == 204, r.text
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": skill_node["id"],
            "to_node_id": asset_node["id"],
            "relationship_type": "RELATED_TO",
        },
    )
    assert r.status_code == 201, r.text

    result = _detect(client, entity["id"])
    by_detector = {o["detector"]: o for o in result["created"]}
    assert "disconnected_clusters" in by_detector
    cluster = by_detector["disconnected_clusters"]
    assert cluster["opportunity_type"] == "Relationship Opportunity"
    assert cluster["source_type"] == "GraphCluster"
    assert "2 nodes" in cluster["description"]
    # The skill itself is still a D1 finding (it has no evidence).
    assert "skills_no_evidence" in by_detector


# --- Detector 2: skills with evidence but no utilization ---
def test_detector2_skill_evidence_no_utilization():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Skill Asset", "Skill")
    _attach_evidence(client, asset["id"])
    skill = _make_skill(
        client, entity["id"], "Evidenced Skill",
        knowledge_asset_id=asset["id"],
    )

    result = _detect(client, entity["id"])
    by_detector = {o["detector"]: o for o in result["created"]}
    # No D1: the skill has evidence. D2 fires instead.
    assert "skills_no_evidence" not in by_detector
    assert "skills_evidence_no_utilization" in by_detector
    opp = by_detector["skills_evidence_no_utilization"]
    assert opp["opportunity_type"] == "Skill Opportunity"
    assert opp["source_type"] == "Skill"
    assert opp["source_id"] == skill["id"]
    # The asset now has evidence, so D4 does not fire for it.
    assert "knowledge_assets_no_relationships" not in by_detector


# --- Detector 3: certifications with evidence but no utilization ---
def test_detector3_cert_evidence_no_utilization():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Cert Asset", "Certification")
    _attach_evidence(client, asset["id"])
    cert = _make_certification(
        client, entity["id"], "Evidenced Certificate",
        knowledge_asset_id=asset["id"],
    )

    result = _detect(client, entity["id"])
    by_detector = {o["detector"]: o for o in result["created"]}
    assert "certifications_evidence_no_utilization" in by_detector
    opp = by_detector["certifications_evidence_no_utilization"]
    assert opp["opportunity_type"] == "Knowledge Opportunity"
    assert opp["source_type"] == "Certification"
    assert opp["source_id"] == cert["id"]


# --- Detector 4: Legacy Asset category maps to Asset Opportunity ---
def test_detector4_legacy_asset_category_maps_to_asset_type():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(
        client, entity["id"], "Legacy Document", "Legacy Asset"
    )

    result = _detect(client, entity["id"])
    assert result["created_count"] == 1
    opp = result["created"][0]
    assert opp["detector"] == "knowledge_assets_no_relationships"
    assert opp["opportunity_type"] == "Asset Opportunity"


# --- Idempotency: a second run creates no duplicates ---
def test_detect_is_idempotent():
    client, _ = _auth_client()
    entity = _make_entity(client)
    _make_skill(client, entity["id"], "Repeat Skill")
    _make_legacy_asset(client, entity["id"], "Repeat Book")

    first = _detect(client, entity["id"])
    assert first["created_count"] == 2
    second = _detect(client, entity["id"])
    assert second["created_count"] == 0
    assert second["dismissed_count"] == 0
    assert len(_list_opportunities(client, entity_id=entity["id"])) == 2


# --- Auto-dismiss: a gap that closes dismisses its opportunity ---
def test_auto_dismiss_on_gap_close():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Closing Asset", "Skill")
    skill = _make_skill(
        client, entity["id"], "Closing Skill",
        knowledge_asset_id=asset["id"],
    )

    first = _detect(client, entity["id"])
    by_detector = {o["detector"]: o for o in first["created"]}
    # The skill has no evidence (D1); the asset has no
    # relationships (D4).
    assert set(by_detector) == {
        "skills_no_evidence",
        "knowledge_assets_no_relationships",
    }
    d1_id = by_detector["skills_no_evidence"]["id"]
    d4_id = by_detector["knowledge_assets_no_relationships"]["id"]

    # Add evidence: the skill's gap closes (and the asset gains a
    # relationship, so its gap closes too). The skill is now
    # evidenced but unconnected, so D2 fires for it instead.
    _attach_evidence(client, asset["id"])
    second = _detect(client, entity["id"])
    assert second["dismissed_count"] == 2
    dismissed_ids = {o["id"] for o in second["dismissed"]}
    assert dismissed_ids == {d1_id, d4_id}
    for opp in second["dismissed"]:
        assert opp["status"] == "dismissed"
        assert "Auto-dismissed" in opp["description"]
    by_detector2 = {o["detector"]: o for o in second["created"]}
    assert set(by_detector2) == {"skills_evidence_no_utilization"}
    assert by_detector2["skills_evidence_no_utilization"]["source_id"] == skill["id"]
    # The auto-dismiss wrote audit entries.
    assert _audit_for(client, d1_id, "opportunity_auto_dismissed")


# --- A gap that stays open is not dismissed ---
def test_open_gap_survives_rerun():
    client, _ = _auth_client()
    entity = _make_entity(client)
    _make_skill(client, entity["id"], "Persistent Skill")

    first = _detect(client, entity["id"])
    opp_id = first["created"][0]["id"]
    second = _detect(client, entity["id"])
    assert second["dismissed_count"] == 0
    r = client.get(f"/api/v1/opportunities/{opp_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "detected"


# --- An accepted opportunity is not duplicated either ---
def test_accepted_opportunity_not_duplicated():
    client, _ = _auth_client()
    entity = _make_entity(client)
    _make_skill(client, entity["id"], "Accepted Skill")

    first = _detect(client, entity["id"])
    opp_id = first["created"][0]["id"]
    r = client.patch(
        f"/api/v1/opportunities/{opp_id}", json={"status": "accepted"}
    )
    assert r.status_code == 200, r.text
    second = _detect(client, entity["id"])
    assert second["created_count"] == 0
    assert len(_list_opportunities(client, entity_id=entity["id"])) == 1


# --- Validation and ownership ---
def test_opportunity_validation_and_ownership():
    client, _ = _auth_client()
    entity = _make_entity(client)
    other_client, _ = _auth_client()

    # Bad opportunity type.
    r = client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": entity["id"],
            "title": "Bad type",
            "opportunity_type": "Not A Type",
            "confidence": 0.5,
        },
    )
    assert r.status_code == 422, r.text
    # Bad status.
    r = client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": entity["id"],
            "title": "Bad status",
            "opportunity_type": "Skill Opportunity",
            "status": "not-a-status",
        },
    )
    assert r.status_code == 422, r.text
    # Unknown entity.
    r = client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": 999999,
            "title": "No entity",
            "opportunity_type": "Skill Opportunity",
        },
    )
    assert r.status_code == 404, r.text
    # Another user's entity: create is forbidden, detect is
    # forbidden, read is forbidden.
    r = other_client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": entity["id"],
            "title": "Not mine",
            "opportunity_type": "Skill Opportunity",
        },
    )
    assert r.status_code == 403, r.text
    r = other_client.post(
        "/api/v1/opportunities/detect", json={"entity_id": entity["id"]}
    )
    assert r.status_code == 403, r.text
    r = other_client.get(f"/api/v1/entities/{entity['id']}/opportunities")
    assert r.status_code == 403, r.text
    # Unknown opportunity.
    r = client.get("/api/v1/opportunities/999999")
    assert r.status_code == 404


# --- List filters ---
def test_opportunity_list_filters():
    client, _ = _auth_client()
    entity = _make_entity(client)
    other = _make_entity(client, name="Other Person")
    _make_skill(client, entity["id"], "Filter Skill")
    _make_legacy_asset(client, other["id"], "Filter Book")
    _detect(client, entity["id"])
    _detect(client, other["id"])

    all_opps = _list_opportunities(client)
    assert len(all_opps) == 2
    by_entity = _list_opportunities(client, entity_id=entity["id"])
    assert len(by_entity) == 1
    assert by_entity[0]["opportunity_type"] == "Skill Opportunity"
    by_type = _list_opportunities(
        client, opportunity_type="Legacy Opportunity"
    )
    assert len(by_type) == 1
    by_status = _list_opportunities(client, status="detected")
    assert len(by_status) == 2
    by_status = _list_opportunities(client, status="accepted")
    assert len(by_status) == 0


# --- OpenAPI contract ---
def test_opportunities_in_openapi():
    client, _ = _auth_client()
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    paths = r.json()["paths"]
    for path in (
        "/api/v1/opportunities",
        "/api/v1/opportunities/detect",
        "/api/v1/opportunities/{opportunity_id}",
        "/api/v1/entities/{entity_id}/opportunities",
    ):
        assert path in paths, path
