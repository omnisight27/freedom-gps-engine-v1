"""Tests for Engine v1 Sprint 6 — the OmniSight Assistant.

Illustrative values used throughout are small, clearly labeled
teaching examples — not real figures.

AT-606 is the formal Sprint 6 acceptance test for the
assistant; the remaining tests cover the other seven intents,
the unknown-question behavior, intent matching, ownership, and
the OpenAPI contract.

The assistant is DETERMINISTIC and EXPLAINABLE: rule-based
intent matching, no LLM, no AI reasoning, nothing predictive.
An unknown question is never guessed at.

The DB-backed endpoint tests use an in-memory SQLite database
via aiosqlite (a test-only dependency — it is NOT used in
production, which runs PostgreSQL). They exercise the real auth
path: create a user, log in, then ask OmniSight with the JWT.
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
from app.schemas.assistant import SUPPORTED_QUESTIONS
from app.services.assistant_service import match_intent
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


@pytest.fixture(scope="module")
def assistant_data():
    """One entity with gaps OmniSight can explain: a skill with
    no evidence, a skill with evidence, a certification with no
    evidence, a legacy asset with no preservation status, two
    state vector snapshots, and one detected opportunity. Plus a
    second entity with a single snapshot, for the
    nothing-to-compare case."""
    client, _user_id = _auth_client()
    r = client.post("/api/v1/entities", json={"type": "person", "name": "Illustrative Person"})
    assert r.status_code == 201, r.text
    entity = r.json()

    async def _seed():
        async with _test_session() as session:
            await seed_registry(session)
            await session.commit()

    asyncio.run(_seed())

    # Two state vector snapshots: financial rises, human flat.
    r = client.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json={"financial": 100.0, "human": 50.0},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/api/v1/state-vector/entities/{entity['id']}",
        json={"financial": 120.0, "human": 50.0},
    )
    assert r.status_code == 201, r.text

    # A knowledge asset with evidence.
    r = client.post(
        "/api/v1/knowledge",
        json={"entity_id": entity["id"], "title": "Illustrative Will", "category": "Document"},
    )
    assert r.status_code == 201, r.text
    will = r.json()
    r = client.post(
        f"/api/v1/knowledge/{will['id']}/evidence",
        json={"source": "Illustrative will document", "source_type": "Document", "confidence": 0.9},
    )
    assert r.status_code == 201, r.text
    will_evidence = r.json()

    # A skill asset with evidence, for the evidenced skill.
    r = client.post(
        "/api/v1/knowledge",
        json={"entity_id": entity["id"], "title": "Illustrative Skill Asset", "category": "Skill"},
    )
    assert r.status_code == 201, r.text
    skill_asset = r.json()
    r = client.post(
        f"/api/v1/knowledge/{skill_asset['id']}/evidence",
        json={"source": "Illustrative skill certificate", "source_type": "Certificate", "confidence": 0.9},
    )
    assert r.status_code == 201, r.text

    # Skills: one with no evidence, one evidenced.
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
    evidenced_skill = r.json()

    # A certification with no evidence.
    r = client.post(
        "/api/v1/certifications",
        json={"entity_id": entity["id"], "title": "Illustrative Unevidenced Certification"},
    )
    assert r.status_code == 201, r.text
    unevidenced_cert = r.json()

    # A legacy asset with no preservation status.
    r = client.post(
        "/api/v1/legacy-assets",
        json={"entity_id": entity["id"], "title": "Illustrative Unpreserved Book", "asset_type": "Book"},
    )
    assert r.status_code == 201, r.text
    unpreserved_legacy = r.json()

    # One detected opportunity for the unevidenced skill.
    r = client.post(
        "/api/v1/opportunities",
        json={
            "entity_id": entity["id"],
            "title": "Skill with no evidence: Illustrative Unevidenced Skill",
            "description": "The skill 'Illustrative Unevidenced Skill' has no supporting evidence.",
            "opportunity_type": "Skill Opportunity",
            "confidence": 0.9,
            "status": "detected",
            "detector": "skills_no_evidence",
            "source_type": "Skill",
            "source_id": unevidenced_skill["id"],
        },
    )
    assert r.status_code == 201, r.text
    opportunity = r.json()

    # A second entity with a single snapshot.
    r = client.post("/api/v1/entities", json={"type": "person", "name": "Illustrative Second Person"})
    assert r.status_code == 201, r.text
    second_entity = r.json()
    r = client.post(
        f"/api/v1/state-vector/entities/{second_entity['id']}",
        json={"financial": 10.0},
    )
    assert r.status_code == 201, r.text

    return {
        "client": client,
        "entity": entity,
        "second_entity": second_entity,
        "will": will,
        "will_evidence": will_evidence,
        "unevidenced_skill": unevidenced_skill,
        "evidenced_skill": evidenced_skill,
        "unevidenced_cert": unevidenced_cert,
        "unpreserved_legacy": unpreserved_legacy,
        "opportunity": opportunity,
    }


def _ask(client, entity_id, question, **extra):
    body = {"question": question, "entity_id": entity_id, **extra}
    r = client.post("/api/v1/assistant/ask", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- Intent matching (no database) ---
def test_match_intent_all_eight():
    """Every supported question maps to its intent."""
    assert match_intent("Show my opportunities.") == "show_opportunities"
    assert match_intent("What changed in my ledger?") == "ledger_changes"
    assert (
        match_intent("What legacy assets lack preservation?")
        == "legacy_no_preservation"
    )
    assert (
        match_intent("Show skills without evidence.")
        == "skills_no_evidence"
    )
    assert (
        match_intent("Show certifications without evidence.")
        == "certifications_no_evidence"
    )
    assert (
        match_intent("What models are experimental?")
        == "experimental_models"
    )
    assert (
        match_intent("Why was this opportunity created?")
        == "opportunity_reason"
    )
    assert (
        match_intent("What evidence supports this record?")
        == "record_evidence"
    )


def test_match_intent_case_and_punctuation():
    """Matching is case-insensitive and punctuation-insensitive."""
    assert match_intent("SHOW SKILLS WITHOUT EVIDENCE") == "skills_no_evidence"
    assert match_intent("show skills without evidence") == "skills_no_evidence"
    assert match_intent("  What models are experimental?  ") == "experimental_models"


def test_match_intent_unknown_returns_none():
    assert match_intent("What will my net worth be in 2030?") is None
    assert match_intent("Tell me a joke.") is None
    assert match_intent("Hello.") is None


def test_supported_questions_count():
    assert len(SUPPORTED_QUESTIONS) == 8


# --- AT-606: OmniSight Question ---
def test_at606_show_skills_without_evidence(assistant_data):
    """'Show skills without evidence' → the correct response:
    the unevidenced skill, not the evidenced one."""
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "Show skills without evidence.")
    assert body["intent"] == "skills_no_evidence"
    names = {s["name"] for s in body["data"]}
    assert "Illustrative Unevidenced Skill" in names
    assert "Illustrative Evidenced Skill" not in names
    assert "no supporting evidence" in body["answer"]
    assert body["sources"] == ["vault_service.query_skills_no_evidence"]


# --- Intent 1: Show my opportunities ---
def test_intent_show_opportunities(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "Show my opportunities.")
    assert body["intent"] == "show_opportunities"
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == assistant_data["opportunity"]["id"]
    assert "1 open opportunity" in body["answer"]


# --- Intent 2: What changed in my ledger? ---
def test_intent_ledger_changes(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "What changed in my ledger?")
    assert body["intent"] == "ledger_changes"
    moved = {m["component"]: m for m in body["data"]}
    assert moved["F"]["previous"] == 100.0
    assert moved["F"]["current"] == 120.0
    assert moved["F"]["delta"] == 20.0
    assert "H" not in moved
    assert "Financial moved from 100.0 to 120.0" in body["answer"]


def test_intent_ledger_changes_single_snapshot(assistant_data):
    """With one snapshot there is nothing to compare yet — the
    assistant says so plainly instead of inventing a change."""
    client = assistant_data["client"]
    entity_id = assistant_data["second_entity"]["id"]
    body = _ask(client, entity_id, "What changed in my ledger?")
    assert body["intent"] == "ledger_changes"
    assert body["data"]["financial"] == 10.0
    assert "Only one state vector snapshot exists" in body["answer"]


# --- Intent 3: Legacy assets lacking preservation ---
def test_intent_legacy_no_preservation(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "What legacy assets lack preservation?")
    assert body["intent"] == "legacy_no_preservation"
    titles = {a["title"] for a in body["data"]}
    assert "Illustrative Unpreserved Book" in titles
    assert "lack a preservation status" in body["answer"]


# --- Intent 5: Certifications without evidence ---
def test_intent_certs_no_evidence(assistant_data):
    """Distinct from Sprint 4 Detector 3 (certifications WITH
    evidence but no utilization): this is certifications with no
    evidence at all."""
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "Show certifications without evidence.")
    assert body["intent"] == "certifications_no_evidence"
    titles = {c["title"] for c in body["data"]}
    assert "Illustrative Unevidenced Certification" in titles
    assert "no supporting evidence" in body["answer"]


# --- Intent 6: Experimental models ---
def test_intent_experimental_models(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "What models are experimental?")
    assert body["intent"] == "experimental_models"
    assert len(body["data"]) == 9
    assert all(m["validation_level"] == "Experimental" for m in body["data"])
    assert "no real-world validation data exists" in body["answer"]


# --- Intent 7: Why was this opportunity created? ---
def test_intent_opportunity_reason_with_id(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    opp_id = assistant_data["opportunity"]["id"]
    body = _ask(
        client,
        entity_id,
        "Why was this opportunity created?",
        opportunity_id=opp_id,
    )
    assert body["intent"] == "opportunity_reason"
    assert body["data"]["detector"] == "skills_no_evidence"
    assert body["data"]["confidence"] == 0.9
    assert "Illustrative Unevidenced Skill" in body["data"]["source"]
    assert "no supporting evidence" in body["data"]["reason"]
    assert "skills_no_evidence" in body["answer"]


def test_intent_opportunity_reason_without_id(assistant_data):
    """Without an opportunity_id, the assistant explains the six
    detectors and lists recent opportunities to choose from."""
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "Why was this opportunity created?")
    assert body["intent"] == "opportunity_reason"
    assert "six detectors" in body["answer"]
    assert len(body["data"]["recent_opportunities"]) == 1
    assert len(body["data"]["detectors"]) == 6


def test_intent_opportunity_reason_unknown_id(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    r = client.post(
        "/api/v1/assistant/ask",
        json={
            "question": "Why was this opportunity created?",
            "entity_id": entity_id,
            "opportunity_id": 999999,
        },
    )
    assert r.status_code == 404, r.text


# --- Intent 8: What evidence supports this record? ---
def test_intent_record_evidence_with_record(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    will = assistant_data["will"]
    body = _ask(
        client,
        entity_id,
        "What evidence supports this record?",
        record_id=will["id"],
        record_type="KnowledgeAsset",
    )
    assert body["intent"] == "record_evidence"
    assert len(body["evidence"]) == 1
    assert body["evidence"][0]["id"] == assistant_data["will_evidence"]["id"]
    assert body["evidence"][0]["source"] == "Illustrative will document"
    assert "supported by 1 piece of evidence" in body["answer"]


def test_intent_record_evidence_skill_without_asset(assistant_data):
    """A skill with no linked knowledge asset has no evidence
    attached — the assistant says so plainly."""
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    skill = assistant_data["unevidenced_skill"]
    body = _ask(
        client,
        entity_id,
        "What evidence supports this record?",
        record_id=skill["id"],
        record_type="Skill",
    )
    assert body["intent"] == "record_evidence"
    assert body["data"]["evidence"] == []
    assert "no linked knowledge asset" in body["answer"]


def test_intent_record_evidence_without_record(assistant_data):
    """Without a record, the assistant explains what evidence is
    and lists recent knowledge assets to choose from."""
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "What evidence supports this record?")
    assert body["intent"] == "record_evidence"
    assert "Evidence is what makes a record trustworthy" in body["answer"]
    titles = {
        a["title"] for a in body["data"]["recent_knowledge_assets"]
    }
    assert "Illustrative Will" in titles


# --- Unknown questions are never guessed at ---
def test_unknown_question_unsupported(assistant_data):
    client = assistant_data["client"]
    entity_id = assistant_data["entity"]["id"]
    body = _ask(client, entity_id, "What will my net worth be in 2030?")
    assert body["intent"] == "unsupported"
    assert "I can't answer that yet" in body["answer"]
    assert len(body["data"]["supported_questions"]) == 8
    assert "Show skills without evidence." in body["data"]["supported_questions"]
    assert body["evidence"] == []
    assert body["sources"] == []


# --- Auth and ownership ---
def test_assistant_requires_auth(assistant_data):
    client = TestClient(app)
    entity_id = assistant_data["entity"]["id"]
    r = client.post(
        "/api/v1/assistant/ask",
        json={"question": "Show my opportunities.", "entity_id": entity_id},
    )
    assert r.status_code == 401


def test_assistant_other_users_entity_403(assistant_data):
    other_client, _ = _auth_client()
    entity_id = assistant_data["entity"]["id"]
    r = other_client.post(
        "/api/v1/assistant/ask",
        json={"question": "Show my opportunities.", "entity_id": entity_id},
    )
    assert r.status_code == 403, r.text


def test_assistant_unknown_entity_404(assistant_data):
    client = assistant_data["client"]
    r = client.post(
        "/api/v1/assistant/ask",
        json={"question": "Show my opportunities.", "entity_id": 999999},
    )
    assert r.status_code == 404, r.text


# --- OpenAPI contract ---
def test_assistant_endpoint_in_openapi():
    assert "/api/v1/assistant/ask" in app.openapi()["paths"]
