"""Tests for Engine v1 Sprint 3 — the Knowledge Graph Layer.

Illustrative values used throughout are small, clearly labeled
teaching examples — not real figures.

Acceptance tests (AT-301 … AT-308) are the formal Sprint 3
acceptance suite; the remaining tests cover node / edge CRUD,
validation, ownership, automatic sync, the backfill, the four
graph inventory queries, the Certification link fix, and the
OpenAPI contract.

The DB-backed endpoint tests use an in-memory SQLite database via
aiosqlite (a test-only dependency — it is NOT used in production,
which runs PostgreSQL). They exercise the real auth path: create a
user, log in, then work the graph with the JWT.
"""

from __future__ import annotations

import asyncio
import ast
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.graph import GraphService, IGraphRepository
from app.graph.neo4j import Neo4jGraphRepository
from app.graph.postgres import PostgresGraphRepository
from app.main import app
from app.models.graph import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationshipType,
)


# --- Enums and models (no database) ---
def test_graph_node_types():
    """The seven node types of Sprint 3."""
    assert {t.value for t in GraphNodeType} == {
        "Entity", "KnowledgeAsset", "Skill", "Certification",
        "EvidenceRecord", "FamilyRecord", "LegacyAsset",
    }


def test_graph_relationship_types():
    assert {t.value for t in GraphRelationshipType} == {
        "OWNS", "HAS_SKILL", "HAS_CERTIFICATION", "HAS_EVIDENCE",
        "HAS_RECORD", "HAS_LEGACY", "RELATED_TO", "MEMBER_OF",
        "CONTRIBUTES_TO",
    }


def test_graph_model_tables():
    assert GraphNode.__tablename__ == "graph_nodes"
    assert GraphEdge.__tablename__ == "graph_edges"
    for col in ("node_type", "source_id", "node_metadata"):
        assert col in GraphNode.__table__.columns
    for col in (
        "from_node_id", "to_node_id", "relationship_type",
        "confidence", "source",
    ):
        assert col in GraphEdge.__table__.columns
    # One node per source record; one edge per triple.
    assert any(
        c.name == "uq_graph_node_source"
        for c in GraphNode.__table__.constraints
    )
    assert any(
        c.name == "uq_graph_edge"
        for c in GraphEdge.__table__.constraints
    )


def test_neo4j_repository_is_a_stub():
    """The Neo4j repository exists with the same interface but
    raises until a Neo4j deployment exists — no server, no driver."""
    with pytest.raises(NotImplementedError, match="not configured"):
        Neo4jGraphRepository()


def test_postgres_repository_honors_the_interface():
    """The PostgreSQL implementation is an IGraphRepository."""
    assert isinstance(PostgresGraphRepository, type)
    assert issubclass(PostgresGraphRepository, object)
    # The protocol is runtime-checkable: a repo instance must
    # satisfy it. (A real instance needs a session; the method
    # set is what this asserts.)
    for method in (
        "create_node", "get_node", "get_node_by_source", "list_nodes",
        "update_node_metadata", "delete_node", "create_edge",
        "get_edge", "get_edge_by_triple", "list_edges",
        "delete_edge", "neighbors",
    ):
        assert callable(getattr(PostgresGraphRepository, method)), method
        assert callable(getattr(Neo4jGraphRepository, method)), method


# --- DB-backed endpoint tests (in-memory SQLite, real auth path) ---
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
# SQLite does not enforce foreign keys by default; the graph relies
# on ON DELETE CASCADE (node -> edges), so enable them for the test
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


def _make_family_record(client: TestClient, entity_id: int, title: str = "Illustrative Biography", **extra) -> dict:
    payload = {"entity_id": entity_id, "title": title, "record_type": "Biography"}
    payload.update(extra)
    r = client.post("/api/v1/family-records", json=payload)
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


def _get_node(client: TestClient, node_id: int) -> dict:
    r = client.get(f"/api/v1/graph/nodes/{node_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _edges(client: TestClient, **params) -> list:
    r = client.get("/api/v1/graph/relationships", params={**params, "limit": 200})
    assert r.status_code == 200, r.text
    return r.json()


# --- AT-301 Node Creation ---
def test_at301_node_creation():
    client, _ = _auth_client()
    entity = _make_entity(client, name="Node Person")
    node = _node_for(client, "Entity", entity["id"])
    assert node["node_type"] == "Entity"
    assert node["source_id"] == entity["id"]
    assert node["node_metadata"]["name"] == "Node Person"
    assert node["node_metadata"]["entity_type"] == "person"

    r = client.get(f"/api/v1/graph/nodes/{node['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == node["id"]


# --- AT-302 Relationship Creation ---
def test_at302_relationship_creation():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Graph Skill")
    entity_node = _node_for(client, "Entity", entity["id"])
    skill_node = _node_for(client, "Skill", skill["id"])

    # The HAS_SKILL edge is created automatically with the skill.
    edges = _edges(
        client,
        from_node_id=entity_node["id"],
        relationship_type="HAS_SKILL",
    )
    assert len(edges) == 1
    assert edges[0]["to_node_id"] == skill_node["id"]
    assert edges[0]["relationship_type"] == "HAS_SKILL"
    assert edges[0]["confidence"] == 1.0

    # And a relationship can be created by hand: a second entity
    # RELATED_TO the first.
    other = _make_entity(client, name="Other Person")
    other_node = _node_for(client, "Entity", other["id"])
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": other_node["id"],
            "to_node_id": entity_node["id"],
            "relationship_type": "RELATED_TO",
            "confidence": 0.8,
            "source": "illustrative link",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["relationship_type"] == "RELATED_TO"
    assert created["confidence"] == 0.8

    r = client.get(
        "/api/v1/graph/relationships",
        params={"from_node_id": other_node["id"]},
    )
    assert r.status_code == 200
    assert any(e["id"] == created["id"] for e in r.json())


# --- AT-303 Evidence Connection ---
def test_at303_evidence_connection():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(
        client, entity["id"], "Evidenced Skill Asset", category="Skill"
    )
    _attach_evidence(client, asset["id"])
    skill = _make_skill(
        client, entity["id"], "Evidenced Graph Skill",
        knowledge_asset_id=asset["id"],
    )
    skill_node = _node_for(client, "Skill", skill["id"])
    asset_node = _node_for(client, "KnowledgeAsset", asset["id"])
    # The evidence node belongs to this asset: find it through the
    # asset's HAS_EVIDENCE edges instead of guessing its id.
    asset_edges = _edges(
        client,
        from_node_id=asset_node["id"],
        relationship_type="HAS_EVIDENCE",
    )
    assert len(asset_edges) == 1
    evidence_node = _get_node(client, asset_edges[0]["to_node_id"])
    assert evidence_node["node_type"] == "EvidenceRecord"

    skill_edges = _edges(
        client,
        from_node_id=skill_node["id"],
        relationship_type="HAS_EVIDENCE",
    )
    assert len(skill_edges) == 1
    assert skill_edges[0]["to_node_id"] == evidence_node["id"]


# --- AT-304 Knowledge Inventory Query ---
def test_at304_knowledge_inventory_query():
    client, _ = _auth_client()
    entity = _make_entity(client)
    evidenced = _make_asset(client, entity["id"], "Evidenced Doc")
    _attach_evidence(client, evidenced["id"])
    unevidenced = _make_asset(client, entity["id"], "Unevidenced Doc")

    r = client.get("/api/v1/graph/queries/knowledge-no-evidence")
    assert r.status_code == 200, r.text
    titles = {row["title"] for row in r.json()}
    assert unevidenced["title"] in titles
    assert evidenced["title"] not in titles


# --- AT-305 Graph Traversal ---
def test_at305_graph_traversal():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(
        client, entity["id"], "Traversal Skill Asset", category="Skill"
    )
    _attach_evidence(client, asset["id"])
    skill = _make_skill(
        client, entity["id"], "Traversal Skill",
        knowledge_asset_id=asset["id"],
    )
    entity_node = _node_for(client, "Entity", entity["id"])
    skill_node = _node_for(client, "Skill", skill["id"])
    asset_edges = _edges(
        client,
        from_node_id=_node_for(
            client, "KnowledgeAsset", asset["id"]
        )["id"],
        relationship_type="HAS_EVIDENCE",
    )
    evidence_node = _get_node(client, asset_edges[0]["to_node_id"])
    assert evidence_node["node_type"] == "EvidenceRecord"

    # Direct path: skill -> evidence (one edge).
    r = client.get(
        "/api/v1/graph/path",
        params={
            "from_node_id": skill_node["id"],
            "to_node_id": evidence_node["id"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert [n["id"] for n in body["nodes"]] == [
        skill_node["id"], evidence_node["id"]
    ]
    assert len(body["edges"]) == 1
    assert body["edges"][0]["relationship_type"] == "HAS_EVIDENCE"

    # Entity -> evidence: two hops, through the skill or the asset.
    r = client.get(
        "/api/v1/graph/path",
        params={
            "from_node_id": entity_node["id"],
            "to_node_id": evidence_node["id"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert len(body["nodes"]) == 3
    assert body["nodes"][0]["id"] == entity_node["id"]
    assert body["nodes"][-1]["id"] == evidence_node["id"]
    assert len(body["edges"]) == 2

    # The neighborhood walk from the entity reaches skill and
    # evidence within two hops.
    r = client.get(
        f"/api/v1/graph/nodes/{entity_node['id']}/traverse",
        params={"depth": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    reached = {n["id"] for n in body["nodes"]}
    assert skill_node["id"] in reached
    assert evidence_node["id"] in reached


# --- AT-306 Audit Verification ---
def test_at306_audit_verification():
    client, _ = _auth_client()
    entity = _make_entity(client)
    other = _make_entity(client, name="Audit Other")
    from_node = _node_for(client, "Entity", other["id"])
    to_node = _node_for(client, "Entity", entity["id"])

    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": from_node["id"],
            "to_node_id": to_node["id"],
            "relationship_type": "CONTRIBUTES_TO",
        },
    )
    assert r.status_code == 201, r.text
    edge = r.json()

    r = client.get(
        "/api/v1/vault/audit-log",
        params={
            "event_type": "graph_relationship_created",
            "record_type": "GraphEdge",
            "record_id": edge["id"],
        },
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["entity_id"] == other["id"]

    r = client.delete(f"/api/v1/graph/relationships/{edge['id']}")
    assert r.status_code == 204, r.text
    r = client.get(
        "/api/v1/vault/audit-log",
        params={
            "event_type": "graph_relationship_deleted",
            "record_type": "GraphEdge",
            "record_id": edge["id"],
        },
    )
    assert r.status_code == 200
    assert len(r.json()) == 1


# --- AT-307 Lineage Verification ---
def test_at307_lineage_verification():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Lineage Skill")
    entity_node = _node_for(client, "Entity", entity["id"])
    edges = _edges(
        client,
        from_node_id=entity_node["id"],
        relationship_type="HAS_SKILL",
    )
    assert len(edges) == 1
    edge = edges[0]
    # Lineage is visible on the edge itself.
    assert edge["source"] == "auto"
    assert edge["confidence"] == 1.0
    assert edge["created_at"] is not None
    assert edge["from_node_id"] == entity_node["id"]
    assert edge["to_node_id"] == _node_for(
        client, "Skill", skill["id"]
    )["id"]


# --- AT-308 Repository Abstraction ---
class _InMemoryGraphRepository:
    """A stub IGraphRepository backed by dicts — no database, no
    PostgreSQL. If the service works against this, it depends
    only on the interface."""

    def __init__(self) -> None:
        self.nodes: dict[int, dict] = {}
        self.edges: dict[int, dict] = {}
        self._next_node = 1
        self._next_edge = 1

    async def create_node(self, *, node_type, source_id, metadata=None):
        node = {
            "id": self._next_node, "node_type": node_type,
            "source_id": source_id, "node_metadata": metadata,
        }
        self.nodes[self._next_node] = node
        self._next_node += 1
        return _DictNode(node)

    async def get_node(self, node_id):
        node = self.nodes.get(node_id)
        return _DictNode(node) if node else None

    async def get_node_by_source(self, node_type, source_id):
        for node in self.nodes.values():
            if (
                node["node_type"] == node_type
                and node["source_id"] == source_id
            ):
                return _DictNode(node)
        return None

    async def list_nodes(self, *, node_type=None, limit=50, offset=0):
        rows = [
            n for n in self.nodes.values()
            if node_type is None or n["node_type"] == node_type
        ]
        return [_DictNode(n) for n in rows[offset:offset + limit]]

    async def update_node_metadata(self, node, metadata):
        self.nodes[node.id]["node_metadata"] = metadata
        return _DictNode(self.nodes[node.id])

    async def delete_node(self, node_id):
        if node_id not in self.nodes:
            return False
        del self.nodes[node_id]
        for edge_id in [
            e for e, row in self.edges.items()
            if row["from_node_id"] == node_id
            or row["to_node_id"] == node_id
        ]:
            del self.edges[edge_id]
        return True

    async def create_edge(self, *, from_node_id, to_node_id,
                          relationship_type, confidence=1.0,
                          source=None):
        edge = {
            "id": self._next_edge, "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "relationship_type": relationship_type,
            "confidence": confidence, "source": source,
        }
        self.edges[self._next_edge] = edge
        self._next_edge += 1
        return _DictEdge(edge)

    async def get_edge(self, edge_id):
        edge = self.edges.get(edge_id)
        return _DictEdge(edge) if edge else None

    async def get_edge_by_triple(self, from_node_id, to_node_id,
                                 relationship_type):
        for edge in self.edges.values():
            if (
                edge["from_node_id"] == from_node_id
                and edge["to_node_id"] == to_node_id
                and edge["relationship_type"] == relationship_type
            ):
                return _DictEdge(edge)
        return None

    async def list_edges(self, *, from_node_id=None, to_node_id=None,
                         relationship_type=None, limit=50, offset=0):
        rows = [
            e for e in self.edges.values()
            if (from_node_id is None or e["from_node_id"] == from_node_id)
            and (to_node_id is None or e["to_node_id"] == to_node_id)
            and (
                relationship_type is None
                or e["relationship_type"] == relationship_type
            )
        ]
        return [_DictEdge(e) for e in rows[offset:offset + limit]]

    async def delete_edge(self, edge_id):
        if edge_id not in self.edges:
            return False
        del self.edges[edge_id]
        return True

    async def neighbors(self, node_id, *, relationship_type=None):
        pairs = []
        for edge in self.edges.values():
            if (
                relationship_type is not None
                and edge["relationship_type"] != relationship_type
            ):
                continue
            if edge["from_node_id"] == node_id:
                other = self.nodes.get(edge["to_node_id"])
            elif edge["to_node_id"] == node_id:
                other = self.nodes.get(edge["from_node_id"])
            else:
                continue
            if other is not None:
                pairs.append((_DictEdge(edge), _DictNode(other)))
        return pairs


class _DictNode:
    """Attribute-style view over a node dict (the service reads
    .id, .node_type, .source_id, .node_metadata)."""

    def __init__(self, data: dict) -> None:
        self.__dict__.update(data)


class _DictEdge:
    """Attribute-style view over an edge dict."""

    def __init__(self, data: dict) -> None:
        self.__dict__.update(data)


def test_at308_repository_abstraction():
    # (a) The service module imports no backend: no postgres, no
    # neo4j anywhere in its imports.
    service_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "graph" / "service.py"
    )
    tree = ast.parse(service_path.read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("postgres" in name for name in imported), imported
    assert not any("neo4j" in name for name in imported), imported

    # (b) The service runs its graph logic against a stub backend
    # that is not PostgreSQL at all.
    async def _run():
        repo = _InMemoryGraphRepository()
        service = GraphService(repo, db=None)
        entity_node = await service.ensure_node(
            "Entity", 1, {"name": "Stub Person"}
        )
        skill_node = await service.ensure_node(
            "Skill", 1, {"name": "Stub Skill"}
        )
        evidence_node = await service.ensure_node(
            "EvidenceRecord", 1, {"source_type": "Document"}
        )
        await service.ensure_edge(
            entity_node, skill_node, "HAS_SKILL"
        )
        await service.ensure_edge(
            skill_node, evidence_node, "HAS_EVIDENCE"
        )
        # Metadata refresh through the same idempotent path.
        refreshed = await service.ensure_node(
            "Entity", 1, {"name": "Stub Person Renamed"}
        )
        assert refreshed.node_metadata["name"] == "Stub Person Renamed"
        assert refreshed.id == entity_node.id
        # Traversal and path work against the stub.
        nodes, edges = await service.traverse(entity_node.id, depth=2)
        assert {n.id for n in nodes} == {
            entity_node.id, skill_node.id, evidence_node.id
        }
        assert len(edges) == 2
        path_nodes, path_edges = await service.find_path(
            entity_node.id, evidence_node.id
        )
        assert [n.id for n in path_nodes] == [
            entity_node.id, skill_node.id, evidence_node.id
        ]
        assert [e.relationship_type for e in path_edges] == [
            "HAS_SKILL", "HAS_EVIDENCE"
        ]
        # No path to an unconnected node.
        lone = await service.ensure_node("Skill", 2, {"name": "Lone"})
        empty_nodes, empty_edges = await service.find_path(
            entity_node.id, lone.id
        )
        assert empty_nodes == [] and empty_edges == []

    asyncio.run(_run())


# --- Automatic nodes and edges for every record type ---
def test_automatic_nodes_for_all_types():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Auto Doc")
    evidence = _attach_evidence(client, asset["id"])
    skill = _make_skill(client, entity["id"], "Auto Skill")
    cert = _make_certification(client, entity["id"], "Auto Certificate")
    family = _make_family_record(client, entity["id"], "Auto Biography")
    legacy = _make_legacy_asset(client, entity["id"], "Auto Book")

    r = client.get("/api/v1/graph/nodes", params={"limit": 200})
    assert r.status_code == 200, r.text
    found = {(n["node_type"], n["source_id"]) for n in r.json()}
    assert ("Entity", entity["id"]) in found
    assert ("KnowledgeAsset", asset["id"]) in found
    assert ("EvidenceRecord", evidence["id"]) in found
    assert ("Skill", skill["id"]) in found
    assert ("Certification", cert["id"]) in found
    assert ("FamilyRecord", family["id"]) in found
    assert ("LegacyAsset", legacy["id"]) in found

    # Node metadata carries the source record's identifying fields.
    asset_node = _node_for(client, "KnowledgeAsset", asset["id"])
    assert asset_node["node_metadata"]["title"] == "Auto Doc"
    assert asset_node["node_metadata"]["category"] == "Document"


def test_automatic_edges():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Edge Doc")
    _attach_evidence(client, asset["id"])
    skill = _make_skill(client, entity["id"], "Edge Skill")
    cert = _make_certification(client, entity["id"], "Edge Certificate")
    family = _make_family_record(client, entity["id"], "Edge Biography")
    legacy = _make_legacy_asset(client, entity["id"], "Edge Book")
    entity_node = _node_for(client, "Entity", entity["id"])

    def _edge_types_from(node_id: int) -> dict[str, set[int]]:
        out: dict[str, set[int]] = {}
        for edge in _edges(client, from_node_id=node_id):
            out.setdefault(edge["relationship_type"], set()).add(
                edge["to_node_id"]
            )
        return out

    by_type = _edge_types_from(entity_node["id"])
    assert by_type["OWNS"] == {
        _node_for(client, "KnowledgeAsset", asset["id"])["id"],
        _node_for(client, "Skill", skill["id"])["id"],
        _node_for(client, "Certification", cert["id"])["id"],
        _node_for(client, "FamilyRecord", family["id"])["id"],
        _node_for(client, "LegacyAsset", legacy["id"])["id"],
    }
    assert by_type["HAS_SKILL"] == {
        _node_for(client, "Skill", skill["id"])["id"]
    }
    assert by_type["HAS_CERTIFICATION"] == {
        _node_for(client, "Certification", cert["id"])["id"]
    }
    assert by_type["HAS_RECORD"] == {
        _node_for(client, "FamilyRecord", family["id"])["id"]
    }
    assert by_type["HAS_LEGACY"] == {
        _node_for(client, "LegacyAsset", legacy["id"])["id"]
    }
    asset_node = _node_for(client, "KnowledgeAsset", asset["id"])
    asset_edges = _edges(
        client,
        from_node_id=asset_node["id"],
        relationship_type="HAS_EVIDENCE",
    )
    assert len(asset_edges) == 1


def test_entity_relationship_graph_edge():
    client, _ = _auth_client()
    family = _make_entity(client, type_="family", name="Graph Family")
    person = _make_entity(client, name="Graph Member")
    r = client.post(
        f"/api/v1/entities/{person['id']}/relationships",
        json={
            "to_entity_id": family["id"],
            "relationship_type": "member_of",
            "confidence": 0.9,
        },
    )
    assert r.status_code == 201, r.text
    person_node = _node_for(client, "Entity", person["id"])
    family_node = _node_for(client, "Entity", family["id"])
    edges = _edges(
        client,
        from_node_id=person_node["id"],
        relationship_type="MEMBER_OF",
    )
    assert len(edges) == 1
    assert edges[0]["to_node_id"] == family_node["id"]
    assert edges[0]["confidence"] == 0.9


# --- Manual relationship validation ---
def test_manual_relationship_validation():
    client, _ = _auth_client()
    entity = _make_entity(client)
    other = _make_entity(client, name="Validation Other")
    node_a = _node_for(client, "Entity", entity["id"])
    node_b = _node_for(client, "Entity", other["id"])

    # Unknown node -> 404.
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": 999999,
            "to_node_id": node_b["id"],
            "relationship_type": "RELATED_TO",
        },
    )
    assert r.status_code == 404, r.text

    # Self-edge -> 400.
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": node_a["id"],
            "to_node_id": node_a["id"],
            "relationship_type": "RELATED_TO",
        },
    )
    assert r.status_code == 400, r.text

    # Invalid type -> 422.
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": node_a["id"],
            "to_node_id": node_b["id"],
            "relationship_type": "LOVES",
        },
    )
    assert r.status_code == 422, r.text

    # Bad confidence -> 422.
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": node_a["id"],
            "to_node_id": node_b["id"],
            "relationship_type": "RELATED_TO",
            "confidence": 1.5,
        },
    )
    assert r.status_code == 422, r.text

    # Create, then duplicate -> 409, then delete -> 204.
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": node_a["id"],
            "to_node_id": node_b["id"],
            "relationship_type": "RELATED_TO",
        },
    )
    assert r.status_code == 201, r.text
    edge = r.json()
    r = client.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": node_a["id"],
            "to_node_id": node_b["id"],
            "relationship_type": "RELATED_TO",
        },
    )
    assert r.status_code == 409, r.text
    r = client.delete(f"/api/v1/graph/relationships/{edge['id']}")
    assert r.status_code == 204, r.text
    assert not _edges(
        client,
        from_node_id=node_a["id"],
        relationship_type="RELATED_TO",
    )
    # Deleting again -> 404.
    r = client.delete(f"/api/v1/graph/relationships/{edge['id']}")
    assert r.status_code == 404, r.text


# --- Subgraph ---
def test_entity_subgraph():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Subgraph Skill")
    r = client.get(f"/api/v1/graph/entities/{entity['id']}/graph")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity["id"]
    assert body["center_node"]["node_type"] == "Entity"
    assert body["center_node"]["source_id"] == entity["id"]
    node_ids = {n["id"] for n in body["nodes"]}
    assert _node_for(client, "Skill", skill["id"])["id"] in node_ids
    assert any(
        e["relationship_type"] == "HAS_SKILL" for e in body["edges"]
    )


# --- Graph inventory queries ---
def test_graph_query_skills_no_evidence():
    client, _ = _auth_client()
    entity = _make_entity(client)
    evidenced_asset = _make_asset(
        client, entity["id"], "Skill Evidence Asset", category="Skill"
    )
    _attach_evidence(client, evidenced_asset["id"])
    unevidenced_asset = _make_asset(
        client, entity["id"], "Skill No-Evidence Asset", category="Skill"
    )
    _make_skill(
        client, entity["id"], "Evidenced Graph Skill",
        knowledge_asset_id=evidenced_asset["id"],
    )
    _make_skill(
        client, entity["id"], "Unevidenced Graph Skill",
        knowledge_asset_id=unevidenced_asset["id"],
    )
    _make_skill(client, entity["id"], "Unlinked Graph Skill")

    r = client.get("/api/v1/graph/queries/skills-no-evidence")
    assert r.status_code == 200, r.text
    names = {row["name"] for row in r.json()}
    assert names == {"Unevidenced Graph Skill", "Unlinked Graph Skill"}


def test_graph_query_certifications_no_evidence():
    client, _ = _auth_client()
    entity = _make_entity(client)
    evidenced_asset = _make_asset(
        client, entity["id"], "Cert Evidence Asset",
        category="Certification",
    )
    _attach_evidence(client, evidenced_asset["id"])
    unevidenced_asset = _make_asset(
        client, entity["id"], "Cert No-Evidence Asset",
        category="Certification",
    )
    _make_certification(
        client, entity["id"], "Evidenced Graph Certificate",
        knowledge_asset_id=evidenced_asset["id"],
    )
    _make_certification(
        client, entity["id"], "Unevidenced Graph Certificate",
        knowledge_asset_id=unevidenced_asset["id"],
    )
    _make_certification(client, entity["id"], "Orphan Graph Certificate")

    r = client.get("/api/v1/graph/queries/certifications-no-evidence")
    assert r.status_code == 200, r.text
    titles = {row["title"] for row in r.json()}
    assert titles == {
        "Unevidenced Graph Certificate", "Orphan Graph Certificate"
    }


def test_graph_query_legacy_no_preservation_status():
    client, _ = _auth_client()
    entity = _make_entity(client)
    _make_legacy_asset(
        client, entity["id"], "Preserved Graph Book",
        preservation_status="preserved",
    )
    _make_legacy_asset(client, entity["id"], "Unset Graph Book")

    r = client.get(
        "/api/v1/graph/queries/legacy-no-preservation-status"
    )
    assert r.status_code == 200, r.text
    titles = {row["title"] for row in r.json()}
    assert titles == {"Unset Graph Book"}


# --- Certification link fix (Sprint 3) ---
def test_certification_asset_link_fix():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(
        client, entity["id"], "Linked Certificate Asset",
        category="Certification",
    )
    _attach_evidence(client, asset["id"])
    cert = _make_certification(
        client, entity["id"], "Linked Certificate",
        knowledge_asset_id=asset["id"],
    )
    # The link is stored on the certification itself now.
    assert cert["knowledge_asset_id"] == asset["id"]

    # The graph query uses the real link, not a title match: this
    # certification is evidenced even though no other record shares
    # its title.
    r = client.get("/api/v1/graph/queries/certifications-no-evidence")
    assert r.status_code == 200, r.text
    assert cert["title"] not in {row["title"] for row in r.json()}
    # The Vault query agrees (link, with the title match kept as a
    # fallback for pre-link records).
    r = client.get("/api/v1/vault/queries/certifications-no-evidence")
    assert r.status_code == 200, r.text
    assert cert["title"] not in {row["title"] for row in r.json()}

    # The graph holds the HAS_EVIDENCE edge from the certification
    # node to the evidence node.
    cert_node = _node_for(client, "Certification", cert["id"])
    edges = _edges(
        client,
        from_node_id=cert_node["id"],
        relationship_type="HAS_EVIDENCE",
    )
    assert len(edges) == 1

    # Moving the link to an unevidenced asset removes the old
    # evidence edge (PATCH None means "no change" in this repo, so
    # a link is moved, not cleared — see vault_service).
    other_asset = _make_asset(
        client, entity["id"], "Other Certificate Asset",
        category="Certification",
    )
    r = client.patch(
        f"/api/v1/certifications/{cert['id']}",
        json={"knowledge_asset_id": other_asset["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_asset_id"] == other_asset["id"]
    assert not _edges(
        client,
        from_node_id=cert_node["id"],
        relationship_type="HAS_EVIDENCE",
    )
    r = client.get("/api/v1/graph/queries/certifications-no-evidence")
    assert cert["title"] in {row["title"] for row in r.json()}


# --- Node lifecycle follows the record ---
def test_node_deleted_with_record():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Doomed Skill")
    assert _node_for(client, "Skill", skill["id"])

    r = client.delete(f"/api/v1/skills/{skill['id']}")
    assert r.status_code == 204, r.text
    r = client.get("/api/v1/graph/nodes", params={"limit": 200})
    assert r.status_code == 200
    assert not any(
        n["node_type"] == "Skill" and n["source_id"] == skill["id"]
        for n in r.json()
    )
    # The OWNS / HAS_SKILL edges went with the node.
    entity_node = _node_for(client, "Entity", entity["id"])
    assert not _edges(
        client,
        from_node_id=entity_node["id"],
        relationship_type="HAS_SKILL",
    )


# --- Ownership ---
def test_graph_ownership():
    client_a, _ = _auth_client()
    client_b, _ = _auth_client()
    entity = _make_entity(client_a, name="Private Person")
    node = _node_for(client_a, "Entity", entity["id"])

    # Another user cannot read the node or the subgraph.
    r = client_b.get(f"/api/v1/graph/nodes/{node['id']}")
    assert r.status_code == 403, r.text
    r = client_b.get(f"/api/v1/graph/entities/{entity['id']}/graph")
    assert r.status_code == 403, r.text
    # And cannot create a relationship from it.
    other = _make_entity(client_b, name="Other User Person")
    other_node = _node_for(client_b, "Entity", other["id"])
    r = client_b.post(
        "/api/v1/graph/relationships",
        json={
            "from_node_id": node["id"],
            "to_node_id": other_node["id"],
            "relationship_type": "RELATED_TO",
        },
    )
    assert r.status_code == 403, r.text


# --- OpenAPI contract ---
def test_openapi_contract():
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    paths = schema["paths"]
    required = [
        "/api/v1/graph/nodes",
        "/api/v1/graph/nodes/{node_id}",
        "/api/v1/graph/nodes/{node_id}/traverse",
        "/api/v1/graph/entities/{entity_id}/graph",
        "/api/v1/graph/path",
        "/api/v1/graph/relationships",
        "/api/v1/graph/relationships/{edge_id}",
        "/api/v1/graph/sync",
        "/api/v1/graph/queries/skills-no-evidence",
        "/api/v1/graph/queries/certifications-no-evidence",
        "/api/v1/graph/queries/knowledge-no-evidence",
        "/api/v1/graph/queries/legacy-no-preservation-status",
    ]
    for path in required:
        assert path in paths, f"missing OpenAPI path: {path}"


# --- Sync / backfill (runs last: it clears the graph tables) ---
def test_sync_backfill_idempotent():
    client, _ = _auth_client()
    entity = _make_entity(client)
    _make_asset(client, entity["id"], "Sync Doc")
    _make_skill(client, entity["id"], "Sync Skill")
    _make_certification(client, entity["id"], "Sync Certificate")
    _make_family_record(client, entity["id"], "Sync Biography")
    _make_legacy_asset(client, entity["id"], "Sync Book")

    async def _clear_graph():
        async with _test_session() as session:
            await session.execute(delete(GraphEdge))
            await session.execute(delete(GraphNode))
            await session.commit()

    asyncio.run(_clear_graph())

    # First run rebuilds everything for this user's entities:
    # 6 nodes (entity + 5 records) and 9 edges (5 OWNS + the four
    # typed edges).
    r = client.post("/api/v1/graph/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nodes_created"] == 6, body
    assert body["edges_created"] == 9, body
    assert body["nodes_total"] >= 6
    assert body["edges_total"] >= 9

    # Second run creates nothing.
    r = client.post("/api/v1/graph/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nodes_created"] == 0, body
    assert body["edges_created"] == 0, body
