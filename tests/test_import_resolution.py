"""Tests for Phase C.1 — imports through the resolver.

Grants and contracts imports resolve their funder / agency to
a shared org entity and link the asset to it (FUNDED_BY /
ISSUED_BY). Synthetic feeds mirror the real schemas; the real
live files are verified by the CLI end-to-end run (scratch
SQLite), per the C.0 pattern — the unit suite never touches
the network.

Acceptance tests 1-3 as integration tests:
  1. NicNacs variants -> one node (service-level; see
     test_entity_resolution.py) and funder variants -> one org
     node here.
  2. Nicholas Long variants -> one node (service-level).
  3. Re-run A+B+C after resolution: duplicate compression
     improves (duplicate groups fall to zero) and discovery
     replay improves (relationship density and findings rise) —
     judged on duplicate compression + discovery replay, with
     raw before/after totals. Total node count is NOT asserted
     to decrease: resolution correctly ADDS the org nodes the
     C.0 graph was missing.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.graph import build_graph_service
from app.main import app
from app.models.entity import Entity
from app.models.graph import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationshipType,
)
from app.models.opportunity import Opportunity
from app.models.user import User
from app.models.vault import KnowledgeAsset
from app.services.contract_import import import_contract_intel
from app.services.entity_resolution import (
    approve_review,
    compute_metrics,
    resolution_measures,
    resolve_or_create,
)
from app.services.grants_import import import_grants

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


def _make_entity(client: TestClient, name: str) -> dict:
    r = client.post(
        "/api/v1/entities", json={"type": "nonprofit", "name": name}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _run_grants_import(feed: dict, entity_id: int, user_id: int) -> dict:
    async with _test_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        entity = (
            await session.execute(select(Entity).where(Entity.id == entity_id))
        ).scalar_one()
        return await import_grants(session, user, entity, feed)


async def _run_contract_import(feed: dict, entity_id: int, user_id: int) -> dict:
    async with _test_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        entity = (
            await session.execute(select(Entity).where(Entity.id == entity_id))
        ).scalar_one()
        return await import_contract_intel(session, user, entity, feed)


async def _rows(model, **where):
    async with _test_session() as session:
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.execute(stmt)).scalars().all())


def _grant_entry(name, funder, url, tier="WARM"):
    return {
        "name": name,
        "funder": funder,
        "level": "Federal",
        "tier": tier,
        "amount": "$10,000",
        "deadline": "June 30, 2027",
        "deadlineISO": "2027-06-30",
        "status": "Open",
        "funds": "Test funds",
        "eligibility": "Nonprofits",
        "url": url,
        "notes": "Synthetic test entry",
    }


def _contract_row(notice_id, title, agency, state="VA"):
    return {
        "title": title,
        "agency": agency,
        "type": "Solicitation",
        "posted": "2026-11-01",
        "deadline": "2026-12-01",
        "naics": "562111",
        "state": state,
        "notice_id": notice_id,
        "nonprofit": True,
        "nonprofit_match": "test match",
    }


def _detect(client: TestClient, entity_id: int) -> dict:
    r = client.post(
        "/api/v1/opportunities/detect", json={"entity_id": entity_id}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_grants_import_collapses_funder_to_one_org_node():
    """Acceptance test 1 (import-level): four 'USDA Rural
    Development' grants -> 1 org node + 4 FUNDED_BY edges. A
    differently-spelled funder at the same domain queues an L4
    review instead of auto-attaching; approving it folds the
    org in and re-points its edge."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Import Resolution Operator")
    feed = {
        "nicnacs": [
            _grant_entry("Grant A", "USDA Rural Development", "https://www.usda.gov/a"),
            _grant_entry("Grant B", "USDA Rural Development", "https://www.usda.gov/b"),
            # Case variant -> L2, not L1.
            _grant_entry("Grant C", "USDA RURAL DEVELOPMENT", "https://www.usda.gov/c"),
            _grant_entry("Grant D", "USDA Rural Development", "https://www.usda.gov/d"),
            _grant_entry("Grant E", "U.S. Dept. of Agriculture", "https://www.usda.gov/e"),
            _grant_entry("Grant F", "Roundhouse Foundation", "https://roundhouse.org/f"),
        ],
        "catalog": [],
    }
    counts = asyncio.run(
        _run_grants_import(feed, entity["id"], user_id)
    )
    assert counts["knowledge_assets_created"] == 6
    assert counts["resolution_new"] == 2  # USDA first + Roundhouse
    assert counts["resolution_l1"] == 2  # identical-spelling repeats
    assert counts["resolution_l2"] == 1  # case variant
    assert counts["resolution_l4"] == 1  # U.S. Dept. of Agriculture
    assert counts["resolution_queued"] == 1
    assert counts["funded_by_edges_created"] == 6

    async def _check():
        async with _test_session() as session:
            orgs = (
                await session.execute(
                    select(Entity).where(
                        Entity.type == "business",
                        Entity.owner_id.is_(None),
                    )
                )
            ).scalars().all()
            names = sorted(o.canonical_name for o in orgs)
            assert names == [
                "Roundhouse Foundation",
                "U.S. Dept. of Agriculture",
                "USDA Rural Development",
            ]
            edges = (
                await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.relationship_type
                        == GraphRelationshipType.FUNDED_BY.value
                    )
                )
            ).scalars().all()
            assert len(edges) == 6
            usda = next(
                o for o in orgs if o.canonical_name == "USDA Rural Development"
            )
            usda_node = (
                await session.execute(
                    select(GraphNode).where(
                        GraphNode.node_type == GraphNodeType.ENTITY.value,
                        GraphNode.source_id == usda.id,
                    )
                )
            ).scalar_one()
            incoming = [e for e in edges if e.to_node_id == usda_node.id]
            assert len(incoming) == 4
            return usda.id

    usda_id = asyncio.run(_check())

    # Approve the queued L4 review: the U.S. Dept. org folds
    # into USDA, its edge re-points, and USDA gains the alias.
    async def _approve():
        async with _test_session() as session:
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            from app.services.entity_resolution import list_pending_reviews

            reviews = await list_pending_reviews(session)
            assert len(reviews) == 1
            result = await approve_review(
                session, review_id=reviews[0].id, user=user
            )
            await session.commit()
            return result

    result = asyncio.run(_approve())
    assert result["canonical_id"] == usda_id

    async def _check_merged():
        async with _test_session() as session:
            orgs = (
                await session.execute(
                    select(Entity).where(
                        Entity.type == "business",
                        Entity.owner_id.is_(None),
                    )
                )
            ).scalars().all()
            assert sorted(o.canonical_name for o in orgs) == [
                "Roundhouse Foundation",
                "USDA Rural Development",
            ]
            usda = next(
                o for o in orgs if o.canonical_name == "USDA Rural Development"
            )
            assert "U.S. Dept. of Agriculture" in (usda.aliases or [])
            edges = (
                await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.relationship_type
                        == GraphRelationshipType.FUNDED_BY.value
                    )
                )
            ).scalars().all()
            assert len(edges) == 6  # all six survive the merge

    asyncio.run(_check_merged())


def test_contracts_import_collapses_agency():
    """Agencies resolve the same way: two 'Department of
    Defense' rows attach at L2; 'Dept. of Defense' (same state,
    shared token 'defense') queues an L4 review; a different
    agency is a new org node."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Contract Resolution Operator")
    feed = {
        "rows": [
            _contract_row("N1", "Solicitation One", "Department of Defense", "VA"),
            # Legal-form variant -> L2, not L1.
            _contract_row("N2", "Solicitation Two", "Department of Defense, Inc.", "VA"),
            _contract_row("N3", "Solicitation Three", "Dept. of Defense", "VA"),
            _contract_row("N4", "Solicitation Four", "Department of Veterans Affairs", "DC"),
        ],
        "next_index": 4,
    }
    counts = asyncio.run(
        _run_contract_import(feed, entity["id"], user_id)
    )
    assert counts["knowledge_assets_created"] == 4
    assert counts["resolution_new"] == 2  # DoD first + VA
    assert counts["resolution_l2"] == 1  # legal-form variant
    assert counts["resolution_l4"] == 1  # Dept. of Defense
    assert counts["resolution_queued"] == 1
    assert counts["issued_by_edges_created"] == 4

    async def _check():
        async with _test_session() as session:
            orgs = (
                await session.execute(
                    select(Entity).where(
                        Entity.type == "business",
                        Entity.owner_id.is_(None),
                    )
                )
            ).scalars().all()
            names = sorted(o.canonical_name for o in orgs)
            assert "Department of Defense" in names
            assert "Dept. of Defense" in names
            assert "Department of Veterans Affairs" in names
            edges = (
                await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.relationship_type
                        == GraphRelationshipType.ISSUED_BY.value
                    )
                )
            ).scalars().all()
            assert len(edges) == 4

    asyncio.run(_check())


def test_acceptance_3_rerun_improves_discovery():
    """Acceptance test 3 (amended): duplicate compression
    improves and discovery replay improves, with raw
    before/after totals. Total node count is NOT asserted to
    decrease — resolution correctly adds the org nodes the
    C.0 graph was missing."""
    client, user_id = _auth_client()
    entity = _make_entity(client, "Replay Operator")

    async def _get_user_inner(uid):
        async with _test_session() as session:
            return await session.get(User, uid)

    user = asyncio.run(_get_user_inner(user_id))

    # Fragmented state, as C.0 left it: duplicate org entities
    # created directly (bypassing the resolver) and grant /
    # contract assets with free-text funders and no org edges.
    async def _seed_fragmented():
        async with _test_session() as session:
            dup_a = Entity(
                type="business",
                name="Replay Funder Org",
                canonical_name="Replay Funder Org",
                aliases=[],
                confidence_score=1.0,
                owner_id=None,
            )
            dup_b = Entity(
                type="business",
                name="REPLAY FUNDER ORG",
                canonical_name="REPLAY FUNDER ORG",
                aliases=[],
                confidence_score=1.0,
                owner_id=None,
            )
            session.add_all([dup_a, dup_b])
            await session.flush()
            graph = build_graph_service(session)
            await graph.sync_entity(dup_a)
            await graph.sync_entity(dup_b)
            for i, (title, content_type, source) in enumerate(
                [
                    ("Replay Grant One", "grant", "grant:replay-funder-org:replay-grant-one"),
                    ("Replay Grant Two", "grant", "grant:replay-funder-org:replay-grant-two"),
                    ("Replay Grant Three", "grant", "grant:replay-funder-org:replay-grant-three"),
                    ("Replay Contract One", "sam-gov-contract", "contract:sam-gov:R1"),
                ]
            ):
                asset = KnowledgeAsset(
                    entity_id=entity["id"],
                    title=title,
                    description=(
                        f"Funder: Replay Funder Org\nURL: https://replay.example.org/{i}"
                        if content_type == "grant"
                        else "Agency: Replay Funder Org\nPlace of performance (state): VA"
                    ),
                    category="Opportunity",
                    content_type=content_type,
                    confidence=0.9,
                    created_by=user_id,
                    source=source,
                    version=1,
                )
                session.add(asset)
                await session.flush()
                await graph.sync_knowledge_asset(asset)
            await session.commit()
            return dup_a.id, dup_b.id

    dup_a_id, dup_b_id = asyncio.run(_seed_fragmented())

    async def _metrics():
        async with _test_session() as session:
            return await compute_metrics(session)

    before = asyncio.run(_metrics())
    assert before["duplicate_groups"] >= 1
    # Module DB is shared with the earlier tests in this file,
    # which already added non-OWNS edges — assert the DELTA,
    # not zero.
    beyond_before = before["relationships_beyond_owns"]
    first_pass = _detect(client, entity["id"])
    d4_before = sum(
        1
        for opp in first_pass.get("created", [])
        if opp.get("detector") == "knowledge_assets_no_relationships"
    )
    assert d4_before == 4  # all four assets isolated

    # Resolution pass: the duplicate orgs merge.
    from app.services.entity_resolution import resolution_pass

    async def _pass():
        async with _test_session() as session:
            result = await resolution_pass(session, user=user, dry_run=False)
            await session.commit()
            return result

    report = asyncio.run(_pass())
    assert len(report["merges"]) == 1
    assert report["merges"][0]["canonical_id"] == dup_a_id
    assert report["merges"][0]["duplicate_id"] == dup_b_id

    # Re-run the imports: the pre-existing assets gain
    # FUNDED_BY / ISSUED_BY edges to the canonical org.
    grants_feed = {
        "nicnacs": [
            _grant_entry("Replay Grant One", "Replay Funder Org", "https://replay.example.org/0"),
            _grant_entry("Replay Grant Two", "Replay Funder Org", "https://replay.example.org/1"),
            _grant_entry("Replay Grant Three", "Replay Funder Org", "https://replay.example.org/2"),
        ],
        "catalog": [],
    }
    contracts_feed = {
        "rows": [
            _contract_row("R1", "Replay Contract One", "Replay Funder Org", "VA"),
        ],
        "next_index": 1,
    }
    g_counts = asyncio.run(
        _run_grants_import(grants_feed, entity["id"], user_id)
    )
    c_counts = asyncio.run(
        _run_contract_import(contracts_feed, entity["id"], user_id)
    )
    assert g_counts["funded_by_edges_created"] == 3
    assert c_counts["issued_by_edges_created"] == 1

    after = asyncio.run(_metrics())
    measures = resolution_measures(before, after)
    # Duplicate compression improves: the duplicate group is gone.
    assert measures["duplicate_groups_after"] == 0
    assert after["duplicate_groups"] == 0
    # Discovery replay improves: density rises from 0, and the
    # D4 findings fall as assets gain non-OWNS edges.
    assert after["relationships_beyond_owns"] == beyond_before + 4
    assert after["relationship_density"] > before["relationship_density"]
    second_pass = _detect(client, entity["id"])
    d4_after = sum(
        1
        for opp in second_pass.get("created", [])
        if opp.get("detector") == "knowledge_assets_no_relationships"
    )
    # The four assets are no longer D4 findings; the second
    # pass creates none for them (and auto-dismisses the four
    # from the first pass — the KNOWN_DETECTORS guard lets D4
    # close its own gaps while import opportunities survive).
    assert d4_after == 0
    dismissed_d4 = sum(
        1
        for opp in second_pass.get("dismissed", [])
        if opp.get("detector") == "knowledge_assets_no_relationships"
    )
    assert dismissed_d4 == 4
    # Raw totals, reported not assumed: in this synthetic setup
    # the org nodes were pre-seeded, so the only nodes removed
    # are the merged duplicates — exactly one per merge, nothing
    # else lost. (In the real E2E the total RISES, because the
    # C.0 graph is missing the org nodes entirely and resolution
    # adds them; what falls in both cases is the duplicate count.)
    assert before["nodes_total"] - after["nodes_total"] == len(
        report["merges"]
    )
    assert measures["duplicate_reduction_pct"] >= 0.0
