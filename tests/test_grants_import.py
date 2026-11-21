"""Tests for Integration Sprint C / EO 007A Phase C part 1 — the
OmniSight -> Freedom GP$ connector: Grants -> Opportunity Records.

The import loads the site's curated grants-data.js (the NicNacs
grants list) through the engine's existing write paths (vault
endpoint for KnowledgeAssets, opportunity_service for
Opportunity records). These tests use a small synthetic feed
that mirrors the real file's two-array shape — the real file is
verified separately by running the CLI against a scratch
database, not by the unit suite.

The DB-backed tests use the same harness as the Phase A/B
suites: an in-memory SQLite database via aiosqlite, the real
auth path (create a user, log in, work with the JWT). Each test
uses a fresh entity so counts are isolated per test.
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
from app.models.graph import GraphEdge, GraphNode
from app.models.opportunity import Opportunity
from app.models.user import User
from app.models.vault import KnowledgeAsset, KnowledgeCategory
from app.services.grants_import import (
    DETECTOR,
    grant_source_key,
    import_grants,
    load_feed,
    parse_feed,
)


# --- Harness (same pattern as the Phase A/B suites) ---
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


# --- Synthetic feed (mirrors the real grants-data.js shape) ---
def _nicnacs(
    name: str,
    *,
    funder: str = "Oregon Community Foundation",
    tier: str = "HOT",
    deadline_iso: str = "2026-12-15",
    amount: str = "$5,000-$40,000",
) -> dict:
    return {
        "name": name,
        "funder": funder,
        "level": "foundation",
        "tier": tier,
        "amount": amount,
        "deadline": "Nov 17 - Dec 15, 2026",
        "deadlineISO": deadline_iso,
        "status": "Window open",
        "funds": "General operating support",
        "eligibility": "Oregon nonprofits",
        "url": "https://example.org/grants",
        "notes": "Strong mission fit.",
    }


def _catalog(
    name: str,
    *,
    funder: str = "WomensNet",
    nonprofit_eligible: bool = True,
) -> dict:
    return {
        "name": name,
        "funder": funder,
        "level": "foundation",
        "categories": ["startup", "women-owned"],
        "states": ["national"],
        "nonprofit_eligible": nonprofit_eligible,
        "forprofit_eligible": True,
        "deadline": "Rolling (monthly)",
        "amount": "$10,000 monthly",
        "url": "https://example.org/amber",
        "blurb": "Monthly grants for women-owned businesses.",
    }


def _feed() -> dict:
    """Two tiered entries (one HOT, one SUBMITTED with a past
    deadline), one entry with no name, one catalog entry that is
    nonprofit-eligible, one that is not, and one catalog entry
    duplicating a tiered grant (same funder, same name)."""
    return {
        "nicnacs": [
            _nicnacs("OCF Community Grants Program"),
            _nicnacs(
                "Josephine County Economic Development Grant",
                funder="Josephine County",
                tier="SUBMITTED",
                deadline_iso="2026-10-30",
                amount="$56,739 requested",
            ),
            {"funder": "Nobody", "tier": "HOT"},
        ],
        "catalog": [
            _catalog("Amber Grant"),
            _catalog(
                "Feed the Soul Restaurant Program",
                funder="Feed the Soul Foundation",
                nonprofit_eligible=False,
            ),
            # Same real-world grant as the tiered entry above:
            # the tiered entry wins.
            _catalog(
                "OCF Community Grants Program",
                funder="Oregon Community Foundation",
            ),
        ],
    }


def _feed_js(feed: dict) -> str:
    """The synthetic feed as a grants-data.js file."""
    import json

    nic = json.dumps(feed["nicnacs"], indent=2)
    cat = json.dumps(feed["catalog"], indent=2)
    return (
        "// test grants file\n"
        f"window.NICNACS_GRANTS = {nic};\n"
        f"window.GRANT_DB = {cat};\n"
    )


async def _run_import(
    feed: dict, entity_id: int, user_id: int
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
        return await import_grants(session, user, entity, feed)


async def _rows(model, **where):
    async with _test_session() as session:
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.execute(stmt)).scalars().all())


# --- The connector ---
def test_grants_use_opportunity_category():
    """Grants are Opportunity records, like the Phase B
    contracts — the category already exists, so no new category
    is needed for this phase."""
    assert KnowledgeCategory.OPPORTUNITY.value == "Opportunity"


def test_parse_feed_reads_both_arrays():
    feed = parse_feed(_feed_js(_feed()))
    assert len(feed["nicnacs"]) == 3
    assert len(feed["catalog"]) == 3
    assert feed["nicnacs"][0]["name"] == "OCF Community Grants Program"


def test_parse_feed_rejects_malformed():
    with pytest.raises(ValueError):
        parse_feed("window.NICNACS_GRANTS = [oops];")
    with pytest.raises(ValueError):
        parse_feed("window.NICNACS_GRANTS = [];")
    with pytest.raises(ValueError):
        parse_feed("no grants here at all")


def test_load_feed_local_file(tmp_path):
    path = tmp_path / "grants-data.js"
    path.write_text(_feed_js(_feed()), encoding="utf-8")
    feed = load_feed(str(path))
    assert len(feed["nicnacs"]) == 3
    assert len(feed["catalog"]) == 3


def test_full_chain():
    """The import: 6 entries seen, 1 incomplete (no name), 1
    catalog duplicate of a tiered grant, 1 catalog grant the
    entity cannot apply for. 4 assets, 3 opportunities (one
    accepted — the SUBMITTED grant), 1 past deadline."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    counts = asyncio.run(_run_import(_feed(), entity["id"], user_id))
    assert counts["entries_seen"] == 6
    assert counts["nicnacs_seen"] == 3
    assert counts["catalog_seen"] == 3
    assert counts["skipped_incomplete"] == 1
    assert counts["skipped_duplicate"] == 1
    assert counts["skipped_not_nonprofit_eligible"] == 1
    assert counts["knowledge_assets_created"] == 4
    assert counts["opportunities_created"] == 3
    assert counts["opportunities_accepted"] == 1
    assert counts["past_deadline"] == 1
    assert counts["stale_assets_not_in_feed"] == 0

    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    assert len(assets) == 4
    assert {a.category for a in assets} == {"Opportunity"}
    assert {a.content_type for a in assets} == {"grant"}
    assert all(a.source.startswith("grant:") for a in assets)
    # The ineligible catalog grant is knowledge, not an
    # opportunity: its asset exists, with the reason recorded.
    ineligible = next(
        a for a in assets if "Feed the Soul" in a.title
    )
    assert "Nonprofit eligible: False" in ineligible.description

    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert len(opps) == 3
    assert {o.detector for o in opps} == {DETECTOR}
    assert {o.source_type for o in opps} == {"KnowledgeAsset"}
    submitted = next(
        o for o in opps if "Josephine County" in o.title
    )
    assert submitted.status == "accepted"
    assert "application is already in" in submitted.description
    # Every opportunity links to its asset.
    asset_ids = {a.id for a in assets}
    assert {o.source_id for o in opps} <= asset_ids

    # Graph: one node + one OWNS edge per asset (Sprint 3
    # auto-sync), plus the entity node.
    nodes = asyncio.run(_rows(GraphNode, node_type="KnowledgeAsset"))
    asset_nodes = [n for n in nodes if n.source_id in asset_ids]
    assert len(asset_nodes) == 4
    assert all(
        n.node_metadata["category"] == "Opportunity"
        for n in asset_nodes
    )
    entity_nodes = asyncio.run(
        _rows(GraphNode, node_type="Entity", source_id=entity["id"])
    )
    assert len(entity_nodes) == 1
    edges = asyncio.run(_rows(GraphEdge, relationship_type="OWNS"))
    assert {
        (e.from_node_id, e.to_node_id) for e in edges
    } >= {
        (entity_nodes[0].id, n.id) for n in asset_nodes
    }


def test_submitted_counts_as_application_in():
    """A SUBMITTED grant imports as accepted, not detected —
    the application is already in, and the funder's decision is
    the outcome to record."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    feed = {"nicnacs": [_nicnacs("County Grant", tier="SUBMITTED")], "catalog": []}
    counts = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert counts["opportunities_created"] == 1
    assert counts["opportunities_accepted"] == 1
    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert opps[0].status == "accepted"


def test_idempotency():
    """A second run creates nothing new."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    feed = _feed()
    first = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert first["knowledge_assets_created"] == 4
    assert first["opportunities_created"] == 3
    second = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert second["knowledge_assets_created"] == 0
    assert second["opportunities_created"] == 0
    assert second["assets_updated"] == 0
    assert second["skipped_duplicate"] == 5  # 4 re-seen + 1 in-file duplicate


def test_dismissed_never_recreated():
    """Dismissing a grant opportunity is the user's decision —
    a re-run must not recreate it."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    asyncio.run(_run_import(_feed(), entity["id"], user_id))
    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    target = opps[0]
    r = client.patch(
        f"/api/v1/opportunities/{target.id}",
        json={"status": "dismissed"},
    )
    assert r.status_code == 200, r.text
    counts = asyncio.run(_run_import(_feed(), entity["id"], user_id))
    assert counts["opportunities_created"] == 0
    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert len(opps) == 3
    assert target.id in {o.id for o in opps if o.status == "dismissed"}


def test_fact_update_versions_asset():
    """A changed deadline updates the asset (version increments)
    and leaves the opportunity alone."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    feed = _feed()
    asyncio.run(_run_import(feed, entity["id"], user_id))
    changed = copy.deepcopy(feed)
    changed["nicnacs"][0]["deadline"] = "Nov 17 - Dec 20, 2026"
    counts = asyncio.run(_run_import(changed, entity["id"], user_id))
    assert counts["assets_updated"] == 1
    assert counts["opportunities_created"] == 0
    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    ocf = next(a for a in assets if "OCF Community" in a.title)
    assert ocf.version == 2
    assert "Dec 20, 2026" in ocf.description


def test_stale_counted_not_dismissed():
    """A grant that disappears from the file is counted as
    stale, not auto-dismissed — the import never closes user
    records."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    feed = _feed()
    asyncio.run(_run_import(feed, entity["id"], user_id))
    smaller = copy.deepcopy(feed)
    smaller["catalog"] = smaller["catalog"][:1]
    counts = asyncio.run(_run_import(smaller, entity["id"], user_id))
    assert counts["stale_assets_not_in_feed"] == 1
    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert len(opps) == 3
    assert all(o.status in ("detected", "accepted") for o in opps)


def test_same_name_different_funder_is_separate():
    """The natural key is funder + name: two grants with the
    same name from different funders are different grants."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "NicNacs")
    feed = {
        "nicnacs": [
            _nicnacs("Community Grant", funder="Funder A"),
            _nicnacs("Community Grant", funder="Funder B"),
        ],
        "catalog": [],
    }
    counts = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert counts["knowledge_assets_created"] == 2
    assert counts["opportunities_created"] == 2
    assert counts["skipped_duplicate"] == 0


def test_source_key_helper():
    assert (
        grant_source_key("OCF Community Grants Program", "Oregon Community Foundation")
        == "grant:oregon-community-foundation:ocf-community-grants-program"
    )
    assert grant_source_key("X", "") == "grant:unknown:x"
