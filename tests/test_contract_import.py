"""Tests for Integration Sprint B / EO 007A Phase B — the OmniSight
-> Freedom GP$ connector: Contract Intel -> Opportunity Records.

The import loads the NexIntel loop's public sam_nonprofit.json
(the SAM.gov nonprofit watch) through the engine's existing
write paths (vault endpoint for KnowledgeAssets,
opportunity_service for Opportunity records). These tests use a
small synthetic feed that mirrors the real file's schema — the
real file is verified separately by running the CLI against a
scratch database, not by the unit suite.

The DB-backed tests use the same harness as the Phase A suite:
an in-memory SQLite database via aiosqlite, the real auth path
(create a user, log in, work with the JWT). Each test uses a
fresh entity so counts are isolated per test.
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
from app.services.contract_import import (
    DETECTOR,
    contract_source_key,
    import_contract_intel,
)


# --- Harness (same pattern as the Phase A suite) ---
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


# --- Synthetic feed (mirrors the real sam_nonprofit.json schema) ---
def _row(
    notice_id: str,
    *,
    title: str = "Trash removal services",
    agency: str = "Dept Of Defense",
    naics: str = "562111",
    state: str = "OR",
    match: str | None = "NAICS 562111 — Solid waste collection",
    deadline: str = "2026-12-14T00:00:00-05:00",
    posted: str = "2026-11-13",
) -> dict:
    return {
        "title": title,
        "agency": agency,
        "type": "Solicitation",
        "posted": posted,
        "deadline": deadline,
        "naics": naics,
        "state": state,
        "notice_id": notice_id,
        "nonprofit": bool(match),
        "nonprofit_match": match,
    }


def _feed() -> dict:
    """Three good rows (two matched, one unmatched), one row
    missing its notice id, one row with no title, and a
    duplicate notice id."""
    return {
        "rows": [
            _row("aaaa0001", title="Trash removal services"),
            _row(
                "bbbb0002",
                title="Grease Trap Service-FY27",
                agency="Veterans Affairs, Department Of",
                naics="562998",
                state="?",
                match="NAICS 562998 — Other waste management",
            ),
            # No match reason: the loop did not match this row.
            _row(
                "cccc0003",
                title="General janitorial services",
                naics="561720",
                state="WA",
                match=None,
            ),
            _row("", title="No notice id at all"),
            _row("dddd0004", title="?"),
            # Duplicate notice id: the same solicitation twice.
            _row("aaaa0001", title="Trash removal services"),
        ],
        "next_index": 6,
    }


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
        return await import_contract_intel(session, user, entity, feed)


async def _rows(model, **where):
    async with _test_session() as session:
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.execute(stmt)).scalars().all())


# --- The connector ---
def test_opportunity_category_exists():
    """The connector's category: a SAM.gov solicitation is an
    Opportunity record, not Research or a Document."""
    assert KnowledgeCategory.OPPORTUNITY.value == "Opportunity"
    assert "Opportunity" in {c.value for c in KnowledgeCategory}


def test_import_creates_full_chain():
    """One solicitation -> KnowledgeAsset (category Opportunity)
    + Opportunity (detected) + graph node and OWNS edge, with
    the loop's match reason preserved verbatim."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity A")
    feed = _feed()
    counts = asyncio.run(_run_import(feed, entity["id"], user_id))

    assert counts["rows_seen"] == 6
    assert counts["with_match"] == 3
    assert counts["without_match"] == 1
    assert counts["skipped_incomplete"] == 2
    assert counts["skipped_duplicate"] == 1
    assert counts["knowledge_assets_created"] == 3
    assert counts["opportunities_created"] == 3
    assert counts["stale_assets_not_in_feed"] == 0

    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    assert len(assets) == 3
    assert {a.category for a in assets} == {"Opportunity"}
    assert {a.content_type for a in assets} == {"sam-gov-contract"}
    assert {a.source for a in assets} == {
        "contract:sam-gov:aaaa0001",
        "contract:sam-gov:bbbb0002",
        "contract:sam-gov:cccc0003",
    }
    by_key = {a.source: a for a in assets}
    # The match reason is preserved verbatim on the asset.
    assert (
        "NAICS 562111 — Solid waste collection"
        in by_key["contract:sam-gov:aaaa0001"].description
    )
    assert "Notice ID: aaaa0001" in by_key["contract:sam-gov:aaaa0001"].description
    assert "Agency: Dept Of Defense" in by_key["contract:sam-gov:aaaa0001"].description
    assert "Response deadline: 2026-12-14T00:00:00-05:00" in (
        by_key["contract:sam-gov:aaaa0001"].description
    )
    assert "none recorded by the loop" in (
        by_key["contract:sam-gov:cccc0003"].description
    )
    # Confidence follows the match: 0.85 matched, 0.5 unmatched.
    assert by_key["contract:sam-gov:aaaa0001"].confidence == 0.85
    assert by_key["contract:sam-gov:cccc0003"].confidence == 0.5

    opportunities = asyncio.run(
        _rows(Opportunity, entity_id=entity["id"])
    )
    assert len(opportunities) == 3
    # The enum cannot represent a contract opportunity, so the
    # least-bad existing type is used — and the detector marks
    # the record as a contract opportunity unambiguously.
    assert {o.opportunity_type for o in opportunities} == {
        "Asset Opportunity"
    }
    assert {o.detector for o in opportunities} == {DETECTOR}
    assert {o.source_type for o in opportunities} == {"KnowledgeAsset"}
    assert {o.status for o in opportunities} == {"detected"}
    assert {o.confidence for o in opportunities} == {0.85, 0.5}
    # Each opportunity points at its asset, and carries the
    # outcome-tracking note: no outcome exists until someone acts.
    asset_ids = {a.id for a in assets}
    assert {o.source_id for o in opportunities} == asset_ids
    assert all(
        "Applied / Not Applied" in (o.description or "")
        for o in opportunities
    )
    assert all(
        "Notice ID: " in (o.description or "") for o in opportunities
    )

    # Every asset got its graph node (type KnowledgeAsset)
    # through the Sprint 3 auto-sync, with an OWNS edge from
    # the entity node.
    nodes = asyncio.run(_rows(GraphNode, node_type="KnowledgeAsset"))
    asset_nodes = [n for n in nodes if n.source_id in asset_ids]
    assert len(asset_nodes) == 3
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


def test_import_is_idempotent():
    """Re-running the import creates nothing new — same counts,
    same rows."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity B")
    feed = _feed()
    first = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert first["knowledge_assets_created"] == 3
    assert first["opportunities_created"] == 3
    second = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert second["knowledge_assets_created"] == 0
    assert second["opportunities_created"] == 0
    assert second["assets_updated"] == 0
    assert second["skipped_duplicate"] == 4
    assert second["stale_assets_not_in_feed"] == 0

    assert (
        len(asyncio.run(_rows(KnowledgeAsset, entity_id=entity["id"])))
        == 3
    )
    assert (
        len(asyncio.run(_rows(Opportunity, entity_id=entity["id"])))
        == 3
    )


def test_rerun_resolves_new_row():
    """A row added to a fresher file creates one new asset +
    opportunity, and nothing else."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity C")
    feed = _feed()
    first = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert first["opportunities_created"] == 3

    fresher = copy.deepcopy(feed)
    fresher["rows"].append(
        _row(
            "eeee0005",
            title="Community food services",
            agency="Agriculture, Department Of",
            naics="624210",
            state="OR",
            match="NAICS 624210 — Community food services",
        )
    )
    second = asyncio.run(_run_import(fresher, entity["id"], user_id))
    assert second["knowledge_assets_created"] == 1
    assert second["opportunities_created"] == 1
    assert second["skipped_duplicate"] == 4
    assert (
        len(asyncio.run(_rows(KnowledgeAsset, entity_id=entity["id"])))
        == 4
    )


def test_rerun_updates_changed_facts():
    """A solicitation re-posted with a new deadline updates the
    asset (version increments) and leaves the opportunity alone."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity D")
    feed = _feed()
    asyncio.run(_run_import(feed, entity["id"], user_id))

    fresher = copy.deepcopy(feed)
    fresher["rows"][0]["deadline"] = "2026-12-21T00:00:00-05:00"
    second = asyncio.run(_run_import(fresher, entity["id"], user_id))
    assert second["assets_updated"] == 1
    assert second["opportunities_created"] == 0
    assert second["knowledge_assets_created"] == 0

    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    updated = [
        a
        for a in assets
        if a.source == "contract:sam-gov:aaaa0001"
    ]
    assert len(updated) == 1
    assert "2026-12-21T00:00:00-05:00" in updated[0].description
    assert updated[0].version == 2


def test_dismissed_opportunity_not_recreated():
    """A dismissed opportunity is never recreated on re-run —
    the dismissal was the user's decision."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity E")
    feed = _feed()
    asyncio.run(_run_import(feed, entity["id"], user_id))

    async def _dismiss():
        async with _test_session() as session:
            opps = (
                await session.execute(
                    select(Opportunity).where(
                        Opportunity.entity_id == entity["id"]
                    )
                )
            ).scalars().all()
            assert len(opps) == 3
            opps[0].status = "dismissed"
            await session.commit()
            return opps[0].id

    dismissed_id = asyncio.run(_dismiss())
    second = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert second["opportunities_created"] == 0
    assert second["skipped_duplicate"] == 4

    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert len(opps) == 3
    assert {o.id for o in opps if o.status == "dismissed"} == {
        dismissed_id
    }


def test_stale_rows_counted_not_dismissed():
    """A row the loop pruned (past deadline) is counted as stale
    on re-run — and its opportunity is left open, never closed
    by the import."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity F")
    feed = _feed()
    first = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert first["stale_assets_not_in_feed"] == 0

    pruned = copy.deepcopy(feed)
    pruned["rows"] = [
        r for r in pruned["rows"] if r.get("notice_id") != "cccc0003"
    ]
    second = asyncio.run(_run_import(pruned, entity["id"], user_id))
    assert second["stale_assets_not_in_feed"] == 1
    assert second["opportunities_created"] == 0

    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert len(opps) == 3
    assert {o.status for o in opps} == {"detected"}


def test_long_title_truncated():
    """A solicitation title over 200 characters is truncated on
    the asset and the opportunity; the full title lives in the
    description."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Import Entity G")
    long_title = "X" * 250
    feed = {"rows": [_row("ffff0006", title=long_title)], "next_index": 0}
    counts = asyncio.run(_run_import(feed, entity["id"], user_id))
    assert counts["knowledge_assets_created"] == 1
    assert counts["opportunities_created"] == 1

    assets = asyncio.run(
        _rows(KnowledgeAsset, entity_id=entity["id"])
    )
    assert len(assets[0].title) <= 200
    assert long_title[:50] in (assets[0].description or "")
    opps = asyncio.run(_rows(Opportunity, entity_id=entity["id"]))
    assert len(opps[0].title) <= 200


def test_source_key_helper():
    """The natural key is stable and names the feed."""
    assert contract_source_key("e1b450638246481d91b462a5e5b1daba") == (
        "contract:sam-gov:e1b450638246481d91b462a5e5b1daba"
    )
