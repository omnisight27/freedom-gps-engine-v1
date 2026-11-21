"""Tests for Engine v1 Sprint 1 — entities, relationships, and the
State Vector Service for entities.

Illustrative values used throughout (small, clearly labeled teaching
numbers — not real figures):

    F=100, H=50, S=40, R=60, L=30, T=20, M=25, I=15, E=10

Hand-computed expected values (same as the state-vector tests):

    Master Index (all coefficients 1.0, the uncalibrated placeholders):
        100+50+40+60+30+20+25+15 − 10 = 330.0
    Prosperity = (100+50+40+60+30) × (20+25+15) / 10 = 1680.0

The DB-backed endpoint tests use an in-memory SQLite database via
aiosqlite (a test-only dependency — it is NOT used in production, which
runs PostgreSQL). They exercise the real auth path: create a user, log
in, create entities and relationships with the JWT, then record state
vectors for the entities.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.entity import (
    Entity,
    EntityRelationship,
    EntityType,
    RelationshipType,
)
from app.models.state_vector import StateVector
from app.schemas.entity import (
    EntityCreate,
    EntityOut,
    RelationshipCreate,
)
from app.schemas.state_vector import StateVectorCreate
from app.services.state_vector_service import (
    compute_master_index_for_snapshot,
    get_entity_state_vector_history,
    get_latest_entity_state_vector,
    record_entity_state_vector,
)

F, H, S, R, L, T, M, I, E = 100, 50, 40, 60, 30, 20, 25, 15, 10


# --- Enums and models (no database) ---
def test_entity_types():
    """The five entity types Engine v1 measures."""
    assert {t.value for t in EntityType} == {
        "person", "family", "business", "nonprofit", "community",
    }


def test_relationship_types():
    """The five relationship types Engine v1 records."""
    assert {t.value for t in RelationshipType} == {
        "owns", "member_of", "works_with", "inherits", "contributes_to",
    }


def test_entity_model_tables():
    assert Entity.__tablename__ == "entities"
    assert EntityRelationship.__tablename__ == "entity_relationships"
    assert "owner_id" in Entity.__table__.columns
    assert "description" in Entity.__table__.columns
    assert "confidence" in EntityRelationship.__table__.columns
    assert "source" in EntityRelationship.__table__.columns
    # StateVector gained an optional entity_id in Sprint 1.
    assert "entity_id" in StateVector.__table__.columns


def test_entity_schema_round_trip():
    """Create → model instance → response model, values intact."""
    payload = EntityCreate(
        type=EntityType.FAMILY,
        name="Illustrative Family",
        description="A teaching example, not a real family.",
    )
    instance = Entity(
        id=1,
        type=payload.type.value,
        name=payload.name,
        description=payload.description,
        owner_id=7,
        # Phase C.1 — Entity Resolution fields (the canonical
        # name defaults to the name when constructed directly).
        canonical_name=payload.name,
        aliases=["Illustrative Fam."],
        confidence_score=1.0,
        created_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
    )
    out = EntityOut.model_validate(instance)
    assert out.type == "family"
    assert out.name == "Illustrative Family"
    assert out.owner_id == 7
    assert out.canonical_name == "Illustrative Family"
    assert out.aliases == ["Illustrative Fam."]
    assert out.confidence_score == 1.0


def test_entity_schema_rejects_invalid_type():
    """Only the five entity types are accepted."""
    with pytest.raises(Exception):
        EntityCreate(type="nation", name="Nope")  # type: ignore[arg-type]


def test_relationship_schema_rejects_bad_confidence():
    """Confidence is a 0–1 weight, not an arbitrary number."""
    with pytest.raises(Exception):
        RelationshipCreate(to_entity_id=2, relationship_type="owns", confidence=1.5)
    ok = RelationshipCreate(to_entity_id=2, relationship_type="owns")
    assert ok.confidence == 1.0


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
    r = client.post(
        "/api/v1/entities",
        json={"type": type_, "name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_create_entity():
    client, user_id = _auth_client()
    body = _make_entity(client, "business", "Illustrative Business")
    assert body["type"] == "business"
    assert body["name"] == "Illustrative Business"
    assert body["owner_id"] == user_id
    assert body["description"] is None


def test_create_entity_invalid_type_rejected():
    client, _ = _auth_client()
    r = client.post("/api/v1/entities", json={"type": "nation", "name": "Nope"})
    assert r.status_code == 422


def test_list_entities_and_filter_by_type():
    client, _ = _auth_client()
    _make_entity(client, "person", "Person One")
    _make_entity(client, "family", "Family One")
    _make_entity(client, "person", "Person Two")

    r = client.get("/api/v1/entities")
    assert r.status_code == 200
    assert len(r.json()) == 3

    r = client.get("/api/v1/entities", params={"type": "person"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["name"] for row in rows} == {"Person One", "Person Two"}

    r = client.get("/api/v1/entities", params={"type": "kingdom"})
    assert r.status_code == 422


def test_get_entity_with_relationships():
    client, _ = _auth_client()
    body = _make_entity(client, "community", "Illustrative Community")
    r = client.get(f"/api/v1/entities/{body['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == body["id"]
    assert data["relationships"] == []


def test_get_entity_not_found():
    client, _ = _auth_client()
    r = client.get("/api/v1/entities/999999")
    assert r.status_code == 404


def test_update_entity():
    client, _ = _auth_client()
    body = _make_entity(client, "nonprofit", "Old Name")
    r = client.patch(
        f"/api/v1/entities/{body['id']}",
        json={"name": "New Name", "description": "Updated description."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "New Name"
    assert r.json()["description"] == "Updated description."
    assert r.json()["type"] == "nonprofit"


def test_entity_access_control():
    client_a, _ = _auth_client()
    client_b, _ = _auth_client()
    body = _make_entity(client_a, "person", "Private Person")
    # Another user cannot read or change it.
    r = client_b.get(f"/api/v1/entities/{body['id']}")
    assert r.status_code == 403
    r = client_b.patch(f"/api/v1/entities/{body['id']}", json={"name": "Hacked"})
    assert r.status_code == 403
    # And cannot see it in their list.
    r = client_b.get("/api/v1/entities")
    assert all(row["id"] != body["id"] for row in r.json())


def test_entities_require_auth():
    client = TestClient(app)
    r = client.get("/api/v1/entities")
    assert r.status_code == 401
    r = client.post("/api/v1/entities", json={"type": "person", "name": "Nope"})
    assert r.status_code == 401


def test_create_relationship():
    client, _ = _auth_client()
    person = _make_entity(client, "person", "Owner Person")
    business = _make_entity(client, "business", "Owned Business")
    r = client.post(
        f"/api/v1/entities/{person['id']}/relationships",
        json={
            "to_entity_id": business["id"],
            "relationship_type": "owns",
            "confidence": 0.9,
            "source": "illustrative record",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["from_entity_id"] == person["id"]
    assert body["to_entity_id"] == business["id"]
    assert body["relationship_type"] == "owns"
    assert body["confidence"] == 0.9


def test_create_relationship_invalid_type_rejected():
    client, _ = _auth_client()
    a = _make_entity(client, "person", "A")
    b = _make_entity(client, "person", "B")
    r = client.post(
        f"/api/v1/entities/{a['id']}/relationships",
        json={"to_entity_id": b["id"], "relationship_type": "rules"},
    )
    assert r.status_code == 422


def test_create_relationship_self_rejected():
    client, _ = _auth_client()
    a = _make_entity(client, "person", "Self")
    r = client.post(
        f"/api/v1/entities/{a['id']}/relationships",
        json={"to_entity_id": a["id"], "relationship_type": "owns"},
    )
    assert r.status_code == 400


def test_create_relationship_unknown_target_rejected():
    client, _ = _auth_client()
    a = _make_entity(client, "person", "A")
    r = client.post(
        f"/api/v1/entities/{a['id']}/relationships",
        json={"to_entity_id": 999999, "relationship_type": "owns"},
    )
    assert r.status_code == 404


def test_list_relationships_and_neighbors():
    client, _ = _auth_client()
    person = _make_entity(client, "person", "Neighbor Person")
    family = _make_entity(client, "family", "Neighbor Family")
    business = _make_entity(client, "business", "Neighbor Business")
    # person is a member of the family; person owns the business.
    client.post(
        f"/api/v1/entities/{person['id']}/relationships",
        json={"to_entity_id": family["id"], "relationship_type": "member_of"},
    )
    client.post(
        f"/api/v1/entities/{person['id']}/relationships",
        json={"to_entity_id": business["id"], "relationship_type": "owns"},
    )

    r = client.get(f"/api/v1/entities/{person['id']}/relationships")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["relationship_type"] for row in rows} == {"member_of", "owns"}
    assert all(row["direction"] == "outgoing" for row in rows)

    # The family's view shows the same edge as incoming.
    r = client.get(f"/api/v1/entities/{family['id']}/relationships")
    assert len(r.json()) == 1
    assert r.json()[0]["direction"] == "incoming"

    # Neighbors carry the entity and the relationship type.
    r = client.get(f"/api/v1/entities/{person['id']}/neighbors")
    assert r.status_code == 200
    neighbors = r.json()
    assert len(neighbors) == 2
    by_name = {n["entity"]["name"]: n for n in neighbors}
    assert by_name["Neighbor Family"]["relationship_type"] == "member_of"
    assert by_name["Neighbor Family"]["direction"] == "outgoing"
    assert by_name["Neighbor Business"]["relationship_type"] == "owns"

    # Detail view includes the relationships too.
    r = client.get(f"/api/v1/entities/{person['id']}")
    assert len(r.json()["relationships"]) == 2


def _state_vector_payload() -> dict:
    return {
        "financial": F, "human": H, "social": S, "resource": R, "legacy": L,
        "trust": T, "memory": M, "intelligence": I, "entropy": E,
        "opportunity": 25, "notes": "illustrative snapshot",
    }


def test_record_and_read_entity_state_vector():
    client, user_id = _auth_client()
    entity = _make_entity(client, "family", "Measured Family")

    r = client.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json=_state_vector_payload(),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["entity_id"] == entity["id"]
    assert body["user_id"] == user_id
    assert body["financial"] == 100
    assert body["entropy"] == 10

    r = client.get(f"/api/v1/state-vector/entities/{entity['id']}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]

    r = client.get(f"/api/v1/state-vector/entities/{entity['id']}/latest")
    assert r.status_code == 200
    assert r.json()["id"] == body["id"]


def test_entity_master_index_from_latest_snapshot():
    client, _ = _auth_client()
    entity = _make_entity(client, "business", "Indexed Business")
    client.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json=_state_vector_payload(),
    )
    r = client.get(f"/api/v1/state-vector/entities/{entity['id']}/master-index")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["master_index"] == 330.0
    assert data["prosperity"] == 1680.0
    assert data["calibrated"] is False
    assert "UNCALIBRATED" in data["note"]


def test_entity_state_vector_latest_404_when_none():
    client, _ = _auth_client()
    entity = _make_entity(client, "person", "Unmeasured Person")
    r = client.get(f"/api/v1/state-vector/entities/{entity['id']}/latest")
    assert r.status_code == 404
    r = client.get(f"/api/v1/state-vector/entities/{entity['id']}/master-index")
    assert r.status_code == 404


def test_entity_state_vector_forbidden_for_other_user():
    client_a, _ = _auth_client()
    client_b, _ = _auth_client()
    entity = _make_entity(client_a, "person", "Private Person")
    r = client_b.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json=_state_vector_payload(),
    )
    assert r.status_code == 403
    r = client_b.get(f"/api/v1/state-vector/entities/{entity['id']}")
    assert r.status_code == 403


def test_state_vector_service_functions():
    """Service layer directly: record, latest, history, master index."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "community", "Service Community")
    entity_id = entity["id"]

    async def _run():
        async with _test_session() as session:
            result = await session.execute(
                select(Entity).where(Entity.id == entity_id)
            )
            entity_obj = result.scalar_one()
            payload = StateVectorCreate(**_state_vector_payload())
            snap1 = await record_entity_state_vector(
                session, user_id=user_id, entity=entity_obj, payload=payload
            )
            payload2 = StateVectorCreate(**_state_vector_payload())
            snap2 = await record_entity_state_vector(
                session, user_id=user_id, entity=entity_obj, payload=payload2
            )
            latest = await get_latest_entity_state_vector(session, entity_id)
            history = await get_entity_state_vector_history(session, entity_id)
            return snap1, snap2, latest, history

    snap1, snap2, latest, history = asyncio.run(_run())
    assert snap1.entity_id == entity_id
    assert latest.id == snap2.id
    assert [row.id for row in history] == [snap2.id, snap1.id]


def test_compute_master_index_for_snapshot():
    """The service computes through the existing engine — same math,
    same uncalibrated warning, no re-implementation."""
    snapshot = StateVector(
        id=1,
        user_id=1,
        entity_id=2,
        recorded_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        financial=F, human=H, social=S, resource=R, legacy=L,
        trust=T, memory=M, intelligence=I, entropy=E,
        opportunity=25, notes="illustrative snapshot",
    )
    result = compute_master_index_for_snapshot(snapshot)
    assert result["master_index"] == 330.0
    assert result["prosperity"] == 1680.0
    assert result["calibrated"] is False
