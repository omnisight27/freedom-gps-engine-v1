"""Tests for Integration Sprint C / EO 007A Phase C part 2 — the
graph learning pass.

The detectors run over the graph the connector populated
(Phases A/B/C imports), through the existing detect path (POST
/api/v1/opportunities/detect -> run_detection). These tests
prove three things: the learning pass creates detector
opportunities from real imported data, it never closes an
opportunity an importer created, and it is idempotent.

The DB-backed tests use the same harness as the Phase A/B/C
suites: an in-memory SQLite database via aiosqlite, the real
auth path (create a user, log in, work with the JWT). Each test
uses a fresh entity so counts are isolated per test.
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
from app.models.entity import Entity
from app.models.opportunity import Opportunity
from app.models.user import User
from app.models.vault import KnowledgeAsset
from app.services.contract_import import import_contract_intel
from app.services.grants_import import import_grants


# --- Harness (same pattern as the Phase A/B/C suites) ---
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
    r = client.post(
        "/api/v1/entities", json={"type": "nonprofit", "name": name}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _run_contract_import(
    feed: dict, entity_id: int, user_id: int
) -> dict:
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
        return await import_contract_intel(session, user, entity, feed)


async def _run_grants_import(
    feed: dict, entity_id: int, user_id: int
) -> dict:
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
        return await import_grants(session, user, entity, feed)


async def _rows(model, **where):
    async with _test_session() as session:
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.execute(stmt)).scalars().all())


def _detect(client: TestClient, entity_id: int) -> dict:
    r = client.post(
        "/api/v1/opportunities/detect", json={"entity_id": entity_id}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _opps(client: TestClient, entity_id: int) -> list[dict]:
    r = client.get(
        "/api/v1/opportunities",
        params={"entity_id": entity_id, "limit": 100},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- The learning pass ---
def test_detection_never_closes_import_opportunities():
    """The reconcile guard (Phase C): a detection run must never
    auto-dismiss an opportunity an importer created. Before the
    guard, the first learning pass over the populated graph
    would have dismissed every Phase B contract opportunity,
    because the detectors cannot know whether the imported gap
    closed — and the import's contract is 'dismissed is the
    user's decision'."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    asyncio.run(
        _run_contract_import(
            {
                "rows": [
                    {
                        "title": "Trash removal services",
                        "agency": "Dept Of Defense",
                        "type": "Solicitation",
                        "posted": "2026-11-13",
                        "deadline": "2026-12-14T00:00:00-05:00",
                        "naics": "562111",
                        "state": "OR",
                        "notice_id": "aaaa0001",
                        "nonprofit": True,
                        "nonprofit_match": "NAICS 562111 — Solid waste collection",
                    }
                ],
                "next_index": 1,
            },
            entity["id"],
            user_id,
        )
    )
    asyncio.run(
        _run_grants_import(
            {
                "nicnacs": [
                    {
                        "name": "OCF Community Grants Program",
                        "funder": "Oregon Community Foundation",
                        "level": "foundation",
                        "tier": "HOT",
                        "amount": "$5,000-$40,000",
                        "deadline": "Nov 17 - Dec 15, 2026",
                        "deadlineISO": "2026-12-15",
                        "status": "Window open",
                        "funds": "General operating support",
                        "eligibility": "Oregon nonprofits",
                        "url": "https://example.org/grants",
                        "notes": "Strong mission fit.",
                    }
                ],
                "catalog": [],
            },
            entity["id"],
            user_id,
        )
    )
    result = _detect(client, entity["id"])
    # Phase C.1: both imported assets now carry a non-OWNS edge
    # (the contract's ISSUED_BY, the grant's FUNDED_BY), so D4
    # no longer fires on them — the graph itself no longer
    # reports them as unconnected. The guard this test exists
    # for is unchanged: detection dismisses nothing, least of
    # all the two import opportunities.
    assert result["created_count"] == 0
    # Nothing is dismissed — least of all the two import
    # opportunities.
    assert result["dismissed_count"] == 0
    opps = _opps(client, entity["id"])
    imports = [
        o
        for o in opps
        if o["detector"]
        in ("contract_intel_sam_gov", "grants_curated_nicnacs")
    ]
    assert len(imports) == 2
    assert all(o["status"] == "detected" for o in imports)


def test_detection_is_idempotent():
    """A second learning pass creates nothing new and dismisses
    nothing. (Phase C.1: the first pass already creates nothing
    — the imported grant asset carries a FUNDED_BY edge, so D4
    does not fire on it.)"""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    asyncio.run(
        _run_grants_import(
            {
                "nicnacs": [
                    {
                        "name": "OCF Community Grants Program",
                        "funder": "Oregon Community Foundation",
                        "level": "foundation",
                        "tier": "HOT",
                        "amount": "$5,000-$40,000",
                        "deadline": "Nov 17 - Dec 15, 2026",
                        "deadlineISO": "2026-12-15",
                        "status": "Window open",
                        "funds": "General operating support",
                        "eligibility": "Oregon nonprofits",
                        "url": "https://example.org/grants",
                        "notes": "Strong mission fit.",
                    }
                ],
                "catalog": [],
            },
            entity["id"],
            user_id,
        )
    )
    first = _detect(client, entity["id"])
    assert first["created_count"] == 0
    second = _detect(client, entity["id"])
    assert second["created_count"] == 0
    assert second["dismissed_count"] == 0


def test_d4_finds_imported_grant_assets():
    """D4 over real imported data. Phase C.1: the funded grants
    carry FUNDED_BY edges, so D4 no longer fires on them — the
    graph itself no longer reports them as unconnected. D4 still
    fires on the genuinely unconnected asset: the catalog grant
    with no funder, which has only its OWNS edge."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    asyncio.run(
        _run_grants_import(
            {
                "nicnacs": [
                    {
                        "name": "Grant One",
                        "funder": "Funder A",
                        "level": "foundation",
                        "tier": "HOT",
                        "amount": "$10,000",
                        "deadline": "Dec 15, 2026",
                        "deadlineISO": "2026-12-15",
                        "status": "Window open",
                        "funds": "Operating support",
                        "eligibility": "Nonprofits",
                        "url": "https://example.org/1",
                        "notes": "",
                    },
                    {
                        "name": "Grant Two",
                        "funder": "Funder B",
                        "level": "state",
                        "tier": "COOL",
                        "amount": "$5,000",
                        "deadline": "Rolling",
                        "deadlineISO": "",
                        "status": "Rolling",
                        "funds": "Project support",
                        "eligibility": "Nonprofits",
                        "url": "https://example.org/2",
                        "notes": "",
                    },
                ],
                "catalog": [
                    {
                        "name": "Grant Three",
                        # No funder: this asset gains no FUNDED_BY
                        # edge, so it is the only D4 finding.
                        "level": "foundation",
                        "categories": ["startup"],
                        "states": ["national"],
                        "nonprofit_eligible": True,
                        "forprofit_eligible": True,
                        "deadline": "Rolling",
                        "amount": "$1,000",
                        "url": "https://example.org/3",
                        "blurb": "A catalog grant.",
                    }
                ],
            },
            entity["id"],
            user_id,
        )
    )
    result = _detect(client, entity["id"])
    d4 = [
        c
        for c in result["created"]
        if c["detector"] == "knowledge_assets_no_relationships"
    ]
    assert len(d4) == 1
    assert d4[0]["title"].endswith("Grant Three")
    assert all(c["source_type"] == "KnowledgeAsset" for c in d4)
    assert all(
        c["opportunity_type"] == "Knowledge Opportunity" for c in d4
    )


def test_d6_finds_nothing_on_connected_graph():
    """D6 over the populated graph: every imported asset is
    owned by the entity, so no component is detached from the
    ledger — the zero is reported, not hidden."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    asyncio.run(
        _run_grants_import(
            {
                "nicnacs": [
                    {
                        "name": "OCF Community Grants Program",
                        "funder": "Oregon Community Foundation",
                        "level": "foundation",
                        "tier": "HOT",
                        "amount": "$5,000-$40,000",
                        "deadline": "Nov 17 - Dec 15, 2026",
                        "deadlineISO": "2026-12-15",
                        "status": "Window open",
                        "funds": "General operating support",
                        "eligibility": "Oregon nonprofits",
                        "url": "https://example.org/grants",
                        "notes": "Strong mission fit.",
                    }
                ],
                "catalog": [],
            },
            entity["id"],
            user_id,
        )
    )
    result = _detect(client, entity["id"])
    assert not [
        c
        for c in result["created"]
        if c["detector"] == "disconnected_clusters"
    ]


def test_closed_detector_gap_still_auto_dismisses():
    """The guard changes only who may be dismissed, not the
    dismiss behavior itself: a detector gap that closes still
    auto-dismisses its opportunity. A bare asset is flagged by
    D4; connecting it with a manual relationship closes the
    gap, and the next pass dismisses the D4 opportunity."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    r = client.post(
        "/api/v1/knowledge",
        json={
            "entity_id": entity["id"],
            "title": "Unconnected research note",
            "description": "A bare asset with no relationships.",
            "category": "Research",
            "content_type": "note",
            "source": "test:bare-note",
        },
    )
    assert r.status_code == 201, r.text
    asset_id = r.json()["id"]
    first = _detect(client, entity["id"])
    assert first["created_count"] == 1
    d4_id = first["created"][0]["id"]
    # Connect the asset: a manual RELATED_TO edge from the asset
    # node to the entity node.
    nodes = client.get(
        "/api/v1/graph/nodes", params={"limit": 200}
    ).json()
    asset_node = next(
        n
        for n in nodes
        if n["node_type"] == "KnowledgeAsset"
        and n["source_id"] == asset_id
    )
    entity_node = next(
        n
        for n in nodes
        if n["node_type"] == "Entity"
        and n["source_id"] == entity["id"]
    )
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": asset_node["id"],
            "to_node_id": entity_node["id"],
            "relationship_type": "RELATED_TO",
            "confidence": 1.0,
        },
    )
    assert r.status_code == 201, r.text
    second = _detect(client, entity["id"])
    assert second["dismissed_count"] == 1
    assert second["dismissed"][0]["id"] == d4_id
    assert second["dismissed"][0]["status"] == "dismissed"
    assert "Auto-dismissed" in second["dismissed"][0]["description"]
