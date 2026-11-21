"""Tests for Engine v1 Phase C.1 — Entity Resolution.

One Entity = One Identity = One Node. The resolver normalizes
names and matches them in layers (L1 exact, L2 normalized, L3
alias, L4 context — queue only, L5 human review); merges fold
a duplicate into a canonical entity with every foreign key
re-pointed and every graph edge preserved or re-pointed.

The DB-backed tests use the same in-memory SQLite harness as
the other suites (aiosqlite + StaticPool, Base.metadata
.create_all, real auth with JWT). Names are unique per test so
the module-shared database cannot leak a match across tests.
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
from app.models.entity import Entity, EntityType
from app.models.entity_resolution import EntityResolutionReview
from app.models.graph import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationshipType,
)
from app.models.opportunity import Opportunity
from app.models.user import User
from app.models.vault import AuditLog, KnowledgeAsset, KnowledgeCategory
from app.services.entity_resolution import (
    approve_review,
    compute_metrics,
    link_asset_to_org,
    list_pending_reviews,
    merge_entities,
    normalize_name,
    reject_review,
    resolution_measures,
    resolve_or_create,
)

# --- Normalization (no database) ---


def test_normalize_name_table():
    """The canonical examples: every spelling of NicNacs is one
    key; legal-form suffixes and the 501(c)(3) designator drop
    out; 'foundation' does not."""
    assert normalize_name("NicNacs") == "nicnacs"
    assert normalize_name("NICNACS") == "nicnacs"
    assert normalize_name("Nic Nacs") == "nicnacs"
    assert normalize_name("NicNacs Inc") == "nicnacs"
    assert normalize_name("NicNacs 501(c)(3)") == "nicnacs"
    assert normalize_name("NicNacs Inc.") == "nicnacs"
    assert normalize_name("The NicNacs") == "nicnacs"
    assert normalize_name("NicNacs LLC") == "nicnacs"
    assert normalize_name("Roundhouse Foundation") == "roundhousefoundation"
    assert normalize_name("Café Olé & Sons, Ltd.") == "cafeolesons"


def test_normalize_n_long_does_not_match_nicholas():
    """An initial is not a name: 'N. Long' normalizes to
    'nlong', which must NOT equal 'nicholaslong' at L2. Only an
    alias (or a human) may assert that identity."""
    assert normalize_name("N. Long") == "nlong"
    assert normalize_name("Nicholas Long") == "nicholaslong"
    assert normalize_name("N. Long") != normalize_name("Nicholas Long")
    assert normalize_name("Nick Long") == "nicklong"
    assert normalize_name("Nic Long") == "niclong"


def test_normalize_token_order_matters():
    """Tokens are NOT sorted: order is part of the identity."""
    assert normalize_name("Long Nicholas") == "longnicholas"
    assert normalize_name("Long Nicholas") != normalize_name("Nicholas Long")


# --- DB-backed tests (in-memory SQLite, real auth path) ---
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


async def _get_user(user_id: int) -> User:
    async with _test_session() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user


async def _run(coro):
    return asyncio.run(coro)


def _resolve(name, entity_type, user, *, owner_id=None, context=None):
    async def _go():
        async with _test_session() as session:
            result = await resolve_or_create(
                session,
                name=name,
                entity_type=entity_type,
                context=context,
                owner_id=owner_id,
                user=user,
            )
            await session.commit()
            return (result.entity.id, result.confidence, result.layer, result.review_id)

    return asyncio.run(_go())


def _node(session, node_type, source_id):
    return session.execute(
        select(GraphNode).where(
            GraphNode.node_type == node_type,
            GraphNode.source_id == source_id,
        )
    )


def test_resolver_l1_l2_layers():
    """Acceptance test 1 (core): NICNACS / Nic Nacs / NicNacs
    501(c)(3) / NicNacs Inc all resolve to ONE entity, and the
    spellings land in its aliases."""
    _, user_id = _auth_client()
    user = asyncio.run(_get_user(user_id))
    ent_id, conf, layer, _ = _resolve(
        "NicNacs", "nonprofit", user, owner_id=user_id
    )
    assert layer == "NEW" and conf == 1.0
    ent_id2, conf, layer, _ = _resolve(
        "NICNACS", "nonprofit", user, owner_id=user_id
    )
    assert layer == "L2" and conf == 0.98 and ent_id2 == ent_id
    ent_id3, conf, layer, _ = _resolve(
        "Nic Nacs", "nonprofit", user, owner_id=user_id
    )
    assert layer == "L2" and ent_id3 == ent_id
    ent_id4, conf, layer, _ = _resolve(
        "NicNacs 501(c)(3)", "nonprofit", user, owner_id=user_id
    )
    assert layer == "L2" and ent_id4 == ent_id
    ent_id5, conf, layer, _ = _resolve(
        "NicNacs Inc", "nonprofit", user, owner_id=user_id
    )
    assert layer == "L2" and ent_id5 == ent_id
    ent_id6, conf, layer, _ = _resolve(
        "NicNacs", "nonprofit", user, owner_id=user_id
    )
    assert layer == "L1" and conf == 1.0 and ent_id6 == ent_id

    async def _check():
        async with _test_session() as session:
            entity = await session.get(Entity, ent_id)
            assert entity.canonical_name == "NicNacs"
            assert set(entity.aliases or []) == {
                "NICNACS",
                "Nic Nacs",
                "NicNacs 501(c)(3)",
                "NicNacs Inc",
            }
            nodes = (
                await session.execute(
                    select(GraphNode).where(
                        GraphNode.node_type == GraphNodeType.ENTITY.value,
                        GraphNode.source_id == ent_id,
                    )
                )
            ).scalars().all()
            assert len(nodes) == 1

    asyncio.run(_check())


def test_resolver_l3_alias_and_n_long_stays_separate():
    """Acceptance test 2 (core): 'Nick Long' resolves to
    'Nicholas Long' through a stored alias (L3, 0.95) — while
    'N. Long' does NOT match and becomes its own entity."""
    _, user_id = _auth_client()
    user = asyncio.run(_get_user(user_id))

    async def _seed():
        async with _test_session() as session:
            entity = Entity(
                type=EntityType.PERSON.value,
                name="Nicholas Long",
                canonical_name="Nicholas Long",
                aliases=["Nic Long", "Nick Long", "N. Long"],
                confidence_score=1.0,
                owner_id=user_id,
            )
            session.add(entity)
            await session.commit()
            await session.refresh(entity)
            return entity.id

    ent_id = asyncio.run(_seed())
    got_id, conf, layer, _ = _resolve(
        "Nick Long", "person", user, owner_id=user_id
    )
    assert layer == "L3" and conf == 0.95 and got_id == ent_id
    got_id2, conf, layer, _ = _resolve(
        "Nic Long", "person", user, owner_id=user_id
    )
    assert layer == "L3" and got_id2 == ent_id
    # "N. Long" IS a stored alias of this entity, so it matches
    # at L3 — the alias asserts the identity. A bare "N. Long"
    # with no alias behind it would be NEW (see below).
    got_id3, conf, layer, _ = _resolve(
        "N. Long", "person", user, owner_id=user_id
    )
    assert layer == "L3" and got_id3 == ent_id

    _, user2_id = _auth_client()
    user2 = asyncio.run(_get_user(user2_id))
    new_id, conf, layer, _ = _resolve(
        "N. Long", "person", user2, owner_id=user2_id
    )
    assert layer == "NEW" and new_id != ent_id


def test_api_create_dedups():
    """POST /entities twice with variant spellings returns the
    same entity, with the spelling recorded as an alias."""
    client, _ = _auth_client()
    r = client.post(
        "/api/v1/entities",
        json={"type": "nonprofit", "name": "Api Dedup Org"},
    )
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["canonical_name"] == "Api Dedup Org"
    assert first["aliases"] == []
    r = client.post(
        "/api/v1/entities",
        json={"type": "nonprofit", "name": "API DEDUP ORG"},
    )
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["id"] == first["id"]
    assert "API DEDUP ORG" in second["aliases"]
    r = client.post(
        "/api/v1/entities",
        json={"type": "nonprofit", "name": "Api Dedup Org Inc"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] == first["id"]
    assert "Api Dedup Org Inc" in r.json()["aliases"]
    # A different type is a different entity — no cross-type
    # collision.
    r = client.post(
        "/api/v1/entities",
        json={"type": "business", "name": "Api Dedup Org"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] != first["id"]


def test_merge_repoints_fks_preserves_aliases_and_edges():
    """merge_entities: FKs re-point, aliases merge, the
    duplicate's node is deleted, and the FUNDED_BY edge is
    re-pointed at the canonical node — not dropped."""
    client, user_id = _auth_client()
    user = asyncio.run(_get_user(user_id))

    async def _setup():
        async with _test_session() as session:
            canonical = Entity(
                type=EntityType.NONPROFIT.value,
                name="Merge Canon Org",
                canonical_name="Merge Canon Org",
                aliases=[],
                confidence_score=1.0,
                owner_id=user_id,
            )
            duplicate = Entity(
                type=EntityType.NONPROFIT.value,
                name="MERGE CANON ORG",
                canonical_name="MERGE CANON ORG",
                # Aliases that normalize DIFFERENTLY from the
                # canonical key: these are what a merge must
                # preserve. The duplicate's own name ("MERGE
                # CANON ORG") normalizes to the canonical key
                # and is correctly NOT stored — L2 matches it
                # already.
                aliases=["Merge Canon Organization", "MCO"],
                confidence_score=1.0,
                owner_id=user_id,
            )
            operator = Entity(
                type=EntityType.NONPROFIT.value,
                name="Merge Operator Org",
                canonical_name="Merge Operator Org",
                aliases=[],
                confidence_score=1.0,
                owner_id=user_id,
            )
            session.add_all([canonical, duplicate, operator])
            await session.flush()
            graph = build_graph_service(session)
            for entity in (canonical, duplicate, operator):
                await graph.sync_entity(entity)
            # An asset + opportunity owned by the duplicate.
            asset = KnowledgeAsset(
                entity_id=duplicate.id,
                title="Merge Test Asset",
                description="Funder: Merge Canon Org\nURL: https://example.org/x",
                category=KnowledgeCategory.OPPORTUNITY.value,
                content_type="grant",
                confidence=0.9,
                created_by=user_id,
                source="grant:test:merge",
                version=1,
            )
            session.add(asset)
            await session.flush()
            await graph.sync_knowledge_asset(asset)
            opp = Opportunity(
                entity_id=duplicate.id,
                title="Merge Test Opportunity",
                opportunity_type="Asset",
                confidence=0.8,
                status="detected",
                detector="test_detector",
                source_type="KnowledgeAsset",
                source_id=asset.id,
            )
            session.add(opp)
            # A grant asset owned by the operator, FUNDED_BY the
            # duplicate org — the edge the merge must preserve.
            grant_asset = KnowledgeAsset(
                entity_id=operator.id,
                title="Merge Grant Asset",
                description="Funder: MERGE CANON ORG\nURL: https://example.org/y",
                category=KnowledgeCategory.OPPORTUNITY.value,
                content_type="grant",
                confidence=0.9,
                created_by=user_id,
                source="grant:test:merge-grant",
                version=1,
            )
            session.add(grant_asset)
            await session.flush()
            await graph.sync_knowledge_asset(grant_asset)
            await link_asset_to_org(
                session,
                asset=grant_asset,
                org=duplicate,
                relationship_type=GraphRelationshipType.FUNDED_BY.value,
                confidence=0.98,
            )
            await session.commit()
            return (
                canonical.id,
                duplicate.id,
                operator.id,
                asset.id,
                opp.id,
                grant_asset.id,
            )

    canon_id, dup_id, op_id, asset_id, opp_id, grant_id = asyncio.run(_setup())

    async def _merge():
        async with _test_session() as session:
            result = await merge_entities(
                session,
                canonical_id=canon_id,
                duplicate_id=dup_id,
                user=user,
            )
            await session.commit()
            return result

    result = asyncio.run(_merge())
    assert result["canonical_id"] == canon_id
    assert result["duplicate_id"] == dup_id
    assert result["moved"].get("KnowledgeAsset") == 1
    assert result["moved"].get("Opportunity") == 1

    async def _check():
        async with _test_session() as session:
            assert await session.get(Entity, dup_id) is None
            canon = await session.get(Entity, canon_id)
            assert set(canon.aliases or []) == {
                "Merge Canon Organization",
                "MCO",
            }
            asset = await session.get(KnowledgeAsset, asset_id)
            assert asset.entity_id == canon_id
            opp = await session.get(Opportunity, opp_id)
            assert opp.entity_id == canon_id
            graph = build_graph_service(session)
            assert (
                await graph.repo.get_node_by_source(
                    GraphNodeType.ENTITY.value, dup_id
                )
            ) is None
            assert (
                await graph.repo.get_node_by_source(
                    GraphNodeType.ENTITY.value, canon_id
                )
            ) is not None
            # The FUNDED_BY edge survived, re-pointed at the
            # canonical node.
            grant_node = await graph.repo.get_node_by_source(
                GraphNodeType.KNOWLEDGE_ASSET.value, grant_id
            )
            canon_node = await graph.repo.get_node_by_source(
                GraphNodeType.ENTITY.value, canon_id
            )
            edge = await graph.repo.get_edge_by_triple(
                grant_node.id,
                canon_node.id,
                GraphRelationshipType.FUNDED_BY.value,
            )
            assert edge is not None
            # The asset's OWNS edge now comes from the canonical.
            asset_node = await graph.repo.get_node_by_source(
                GraphNodeType.KNOWLEDGE_ASSET.value, asset_id
            )
            owns = await graph.repo.get_edge_by_triple(
                canon_node.id,
                asset_node.id,
                GraphRelationshipType.OWNS.value,
            )
            assert owns is not None
            # An audit entry records the merge.
            logs = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.event_type == "entity_merged",
                        AuditLog.entity_id == canon_id,
                    )
                )
            ).scalars().all()
            assert len(logs) == 1

    asyncio.run(_check())


def test_compute_metrics_and_measures():
    """Metrics count duplicates honestly, and the Architect's
    measures use the corrected arithmetic (5 -> 1 is 80%)."""
    _, user_id = _auth_client()
    user = asyncio.run(_get_user(user_id))

    async def _seed_and_measure():
        async with _test_session() as session:
            before = await compute_metrics(session)
            for spelling in (
                "Metric Dup Org",
                "METRIC DUP ORG",
                "Metric Dup Org Inc",
            ):
                session.add(
                    Entity(
                        type=EntityType.BUSINESS.value,
                        name=spelling,
                        canonical_name=spelling,
                        aliases=[],
                        confidence_score=1.0,
                        owner_id=None,
                    )
                )
            await session.commit()
            seeded = await compute_metrics(session)
            assert seeded["duplicate_groups"] == before["duplicate_groups"] + 1
            assert seeded["duplicate_entities"] == before["duplicate_entities"] + 3
            return before, seeded

    before, seeded = asyncio.run(_seed_and_measure())
    # The Canon example, corrected: 5 -> 1 is 80%, not 90%.
    measures = resolution_measures(
        {"entities_total": 5, "duplicate_entities": 5, "duplicate_groups": 1,
         "relationship_density": 0.0, "relationships_beyond_owns": 0},
        {"entities_total": 1, "duplicate_entities": 0, "duplicate_groups": 0,
         "relationship_density": 0.4, "relationships_beyond_owns": 2},
    )
    assert measures["duplicate_reduction_pct"] == 80.0
    assert measures["duplicate_compression_ratio"] == 5.0
    assert measures["entity_resolution_rate"] == 5.0
    assert measures["duplicate_groups_after"] == 0


def test_l4_queue_approve_and_reject():
    """L4 never auto-attaches: a domain-equal funder spelling
    creates a new entity and queues a review; approve merges
    it, reject suppresses re-queuing."""
    client, user_id = _auth_client()
    user = asyncio.run(_get_user(user_id))

    async def _setup():
        async with _test_session() as session:
            operator = Entity(
                type=EntityType.NONPROFIT.value,
                name="L4 Operator Org",
                canonical_name="L4 Operator Org",
                aliases=[],
                confidence_score=1.0,
                owner_id=user_id,
            )
            session.add(operator)
            await session.flush()
            graph = build_graph_service(session)
            await graph.sync_entity(operator)
            asset = KnowledgeAsset(
                entity_id=operator.id,
                title="L4 Grant Asset",
                description="Funder: L4 Funder Org\nURL: https://l4funder.example.org/x",
                category=KnowledgeCategory.OPPORTUNITY.value,
                content_type="grant",
                confidence=0.9,
                created_by=user_id,
                source="grant:test:l4",
                version=1,
            )
            session.add(asset)
            await session.flush()
            await graph.sync_knowledge_asset(asset)
            await session.commit()
            return operator.id, asset.id

    op_id, asset_id = asyncio.run(_setup())

    # Seed the org via a direct resolve (NEW), then link the
    # asset to it so the domain signal has an edge to follow.
    org_id, _, layer, _ = _resolve(
        "L4 Funder Org", "business", user, owner_id=None
    )
    assert layer == "NEW"

    async def _link():
        async with _test_session() as session:
            asset = await session.get(KnowledgeAsset, asset_id)
            org = await session.get(Entity, org_id)
            created = await link_asset_to_org(
                session,
                asset=asset,
                org=org,
                relationship_type=GraphRelationshipType.FUNDED_BY.value,
                confidence=1.0,
            )
            await session.commit()
            return created

    assert asyncio.run(_link()) is True

    # A differently-spelled funder at the same domain: L4,
    # new entity, review queued — never auto-attached.
    new_id, conf, layer, review_id = _resolve(
        "L4 Funder Organization",
        "business",
        user,
        owner_id=None,
        context={"domain": "l4funder.example.org"},
    )
    assert layer == "L4" and conf == 0.85
    assert review_id is not None and new_id != org_id

    async def _approve():
        async with _test_session() as session:
            result = await approve_review(
                session, review_id=review_id, user=user
            )
            await session.commit()
            return result

    result = asyncio.run(_approve())
    assert result["canonical_id"] == org_id
    assert result["duplicate_id"] == new_id

    async def _check_approved():
        async with _test_session() as session:
            assert await session.get(Entity, new_id) is None
            org = await session.get(Entity, org_id)
            assert "L4 Funder Organization" in (org.aliases or [])
            review = await session.get(EntityResolutionReview, review_id)
            assert review.status == "approved"

    asyncio.run(_check_approved())

    # Reject path: a second L4 pair, rejected, then the pass
    # must not re-queue the same pair.
    org2_id, _, layer, _ = _resolve(
        "L4 Second Org", "business", user, owner_id=None
    )
    assert layer == "NEW"

    async def _link2():
        async with _test_session() as session:
            asset = KnowledgeAsset(
                entity_id=op_id,
                title="L4 Second Grant Asset",
                description="Funder: L4 Second Org\nURL: https://l4second.example.org/y",
                category=KnowledgeCategory.OPPORTUNITY.value,
                content_type="grant",
                confidence=0.9,
                created_by=user_id,
                source="grant:test:l4-second",
                version=1,
            )
            session.add(asset)
            await session.flush()
            graph = build_graph_service(session)
            await graph.sync_knowledge_asset(asset)
            org2 = await session.get(Entity, org2_id)
            await link_asset_to_org(
                session,
                asset=asset,
                org=org2,
                relationship_type=GraphRelationshipType.FUNDED_BY.value,
                confidence=1.0,
            )
            await session.commit()

    asyncio.run(_link2())
    new2_id, conf, layer, review2_id = _resolve(
        "L4 Second Organization",
        "business",
        user,
        owner_id=None,
        context={"domain": "l4second.example.org"},
    )
    assert layer == "L4" and review2_id is not None

    async def _reject():
        async with _test_session() as session:
            review = await reject_review(
                session, review_id=review2_id, user=user
            )
            await session.commit()
            return review.status

    assert asyncio.run(_reject()) == "rejected"

    # The API surfaces the queue: only the rejected review is
    # not pending, so the pending list is empty for this pair —
    # and a second resolve of the same pair does not re-queue.
    _, _, layer, review3_id = _resolve(
        "L4 Second Organization",
        "business",
        user,
        owner_id=None,
        context={"domain": "l4second.example.org"},
    )
    # The proposed entity still exists under that spelling, so
    # the re-resolve attaches at L1 — no new review, and the
    # rejected pair stays rejected.
    assert layer == "L1" and review3_id is None


def test_review_endpoints():
    """GET /entities/resolution-reviews lists pending reviews;
    approve/reject act through the API."""
    client, user_id = _auth_client()
    user = asyncio.run(_get_user(user_id))

    async def _seed_review():
        async with _test_session() as session:
            review = EntityResolutionReview(
                proposed_name="Endpoint Proposed Org",
                normalized_name="endpointproposedorg",
                entity_type="business",
                matched_entity_id=None,
                layer="L4",
                confidence=0.85,
                context={"signal": "domain", "domain": "endpoint.example.org"},
            )
            session.add(review)
            await session.commit()
            await session.refresh(review)
            return review.id

    review_id = asyncio.run(_seed_review())
    r = client.get("/api/v1/entities/resolution-reviews?status=pending")
    assert r.status_code == 200, r.text
    assert any(item["id"] == review_id for item in r.json())
    r = client.post(f"/api/v1/entities/resolution-reviews/{review_id}/reject")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    r = client.post(f"/api/v1/entities/resolution-reviews/{review_id}/reject")
    assert r.status_code == 409, r.text
    r = client.post("/api/v1/entities/resolution-reviews/999999/approve")
    assert r.status_code == 404, r.text
