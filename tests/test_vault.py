"""Tests for Engine v1 Sprint 2 — the Knowledge Vault (formal
definition).

Illustrative values used throughout are small, clearly labeled
teaching examples — not real figures. Estimated impact is measured
in abstract units, never dollars.

Acceptance tests (AT-001 … AT-008) are the formal Sprint 2
acceptance suite; the remaining tests cover per-model CRUD,
validation, ownership, lineage, the audit log, and the four
Sprint 2 missing-edge queries.

The DB-backed endpoint tests use an in-memory SQLite database via
aiosqlite (a test-only dependency — it is NOT used in production,
which runs PostgreSQL). They exercise the real auth path: create a
user, log in, then work the Vault with the JWT.
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
from app.models.vault import (
    AuditLog,
    Certification,
    EvidenceRecord,
    FamilyRecord,
    FamilyRecordType,
    KnowledgeAsset,
    KnowledgeCategory,
    LegacyAsset,
    LegacyAssetType,
    Skill,
    EvidenceSourceType,
)
from app.schemas.vault import (
    EvidenceCreate,
    KnowledgeAssetCreate,
)


# --- Enums and models (no database) ---
def test_knowledge_categories():
    """The ten KnowledgeAsset categories.

    "Prediction" was added by the OmniSight connector (EO 007A
    Phase A): a NexIntel pick is a prediction record, distinct
    from "Research" and "Historical Record"."""
    assert {c.value for c in KnowledgeCategory} == {
        "Skill", "Certification", "Document", "Research", "Process",
        "Lesson", "Historical Record", "Family Record", "Legacy Asset",
        "Prediction",
    }


def test_evidence_source_types():
    assert {t.value for t in EvidenceSourceType} == {
        "Document", "Transcript", "Certificate", "User Assertion",
        "Government Record", "Archive Record", "Manual Entry",
    }


def test_family_record_types():
    assert {t.value for t in FamilyRecordType} == {
        "Biography", "Genealogy", "Historical Event", "Oral History",
        "Artifact", "Letter",
    }


def test_legacy_asset_types():
    assert {t.value for t in LegacyAssetType} == {
        "Book", "Patent", "Research", "Archive", "Training",
        "Community Project", "IP",
    }


def test_vault_model_tables():
    assert KnowledgeAsset.__tablename__ == "knowledge_assets"
    assert EvidenceRecord.__tablename__ == "evidence_records"
    assert Skill.__tablename__ == "skills"
    assert Certification.__tablename__ == "certifications"
    assert FamilyRecord.__tablename__ == "family_records"
    assert LegacyAsset.__tablename__ == "legacy_assets"
    assert AuditLog.__tablename__ == "audit_log"
    # Lineage columns on the unified asset.
    for col in ("created_by", "source", "version"):
        assert col in KnowledgeAsset.__table__.columns
    # Evidence attaches to an asset, not to an entity.
    assert "knowledge_asset_id" in EvidenceRecord.__table__.columns
    assert "entity_id" not in EvidenceRecord.__table__.columns
    # Skill links to its asset representation for evidence.
    assert "knowledge_asset_id" in Skill.__table__.columns
    assert "verified" in Skill.__table__.columns
    # No out-of-scope fields anywhere.
    assert "monetized" not in Skill.__table__.columns
    assert "opportunity_id" not in Skill.__table__.columns
    assert "utilized" not in Certification.__table__.columns
    assert "related_entity_id" not in FamilyRecord.__table__.columns
    assert "preservation_plan" not in FamilyRecord.__table__.columns
    assert "preservation_status" in LegacyAsset.__table__.columns
    assert "estimated_impact" in LegacyAsset.__table__.columns


def test_knowledge_asset_schema_round_trip():
    payload = KnowledgeAssetCreate(
        entity_id=1,
        title="Illustrative Document",
        description="A teaching example, not a real document.",
        category=KnowledgeCategory.DOCUMENT,
        source="illustrative source",
        confidence=0.8,
    )
    assert payload.category.value == "Document"
    assert payload.status == "active"
    assert payload.confidence == 0.8


def test_knowledge_schema_rejects_invalid_category():
    with pytest.raises(Exception):
        KnowledgeAssetCreate(entity_id=1, title="Nope", category="Nation")  # type: ignore[arg-type]


def test_evidence_schema_rejects_bad_confidence():
    with pytest.raises(Exception):
        EvidenceCreate(source="x", source_type="Document", confidence=1.5)


# --- DB-backed endpoint tests (in-memory SQLite, real auth path) ---
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
# SQLite does not enforce foreign keys by default; the Vault relies
# on ON DELETE CASCADE (asset -> evidence) and ON DELETE SET NULL
# (asset -> skill link), so enable them for the test database. The
# production database is PostgreSQL, where they are enforced.
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


# --- AT-001 Knowledge Asset Lifecycle ---
def test_at001_knowledge_asset_lifecycle():
    client, _ = _auth_client()
    entity = _make_entity(client)
    created = _make_asset(
        client, entity["id"], "Lifecycle Document",
        category="Document", description="Stored, then retrieved.",
        source="illustrative source", confidence=0.75,
    )
    assert created["version"] == 1
    assert created["status"] == "active"
    assert created["category"] == "Document"

    r = client.get(f"/api/v1/knowledge/{created['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == created["id"]
    assert body["title"] == "Lifecycle Document"
    assert body["description"] == "Stored, then retrieved."
    assert body["source"] == "illustrative source"
    assert body["confidence"] == 0.75


# --- AT-002 Evidence Attachment ---
def test_at002_evidence_attachment():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Evidenced Document")

    r = client.get(f"/api/v1/knowledge/{asset['id']}/lineage")
    assert r.status_code == 200
    assert r.json()["evidence_count"] == 0

    _attach_evidence(client, asset["id"], confidence=0.9)
    r = client.get(f"/api/v1/knowledge/{asset['id']}/lineage")
    assert r.json()["evidence_count"] == 1

    _attach_evidence(client, asset["id"], source_type="Transcript", confidence=0.6)
    r = client.get(f"/api/v1/knowledge/{asset['id']}/lineage")
    assert r.json()["evidence_count"] == 2

    r = client.get(f"/api/v1/knowledge/{asset['id']}/evidence")
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert {row["source_type"] for row in r.json()} == {"Document", "Transcript"}


# --- AT-003 Skill Verification ---
def test_at003_skill_verification():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(
        client, entity["id"], "Illustrative Skill Asset", category="Skill"
    )
    _attach_evidence(client, asset["id"])
    skill = _make_skill(
        client, entity["id"], "Illustrative Skill",
        knowledge_asset_id=asset["id"],
    )
    assert skill["verified"] is False

    r = client.patch(f"/api/v1/skills/{skill['id']}", json={"verified": True})
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True

    r = client.get(f"/api/v1/skills/{skill['id']}")
    assert r.status_code == 200
    assert r.json()["verified"] is True


# --- AT-004 Legacy Asset Preservation ---
def test_at004_legacy_asset_preservation():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_legacy_asset(client, entity["id"], "Preserved Book")
    assert asset["preservation_status"] is None

    r = client.patch(
        f"/api/v1/legacy-assets/{asset['id']}",
        json={"preservation_status": "preserved"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["preservation_status"] == "preserved"

    r = client.get(
        "/api/v1/vault/audit-log",
        params={
            "event_type": "legacy_preservation_updated",
            "record_type": "LegacyAsset",
            "record_id": asset["id"],
        },
    )
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["entity_id"] == entity["id"]


# --- AT-005 Family Record Retrieval ---
def test_at005_family_record_retrieval():
    client, _ = _auth_client()
    entity = _make_entity(client)
    for i in range(3):
        _make_family_record(client, entity["id"], f"Biography {i+1}")

    r = client.get(
        f"/api/v1/entities/{entity['id']}/family-records",
        params={"limit": 2, "offset": 0},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get(
        f"/api/v1/entities/{entity['id']}/family-records",
        params={"limit": 2, "offset": 2},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["title"] == "Biography 3"


# --- AT-006 Entity Knowledge Inventory ---
def test_at006_entity_knowledge_inventory():
    client, _ = _auth_client()
    entity = _make_entity(client)
    for i in range(3):
        _make_skill(client, entity["id"], f"Inventory Skill {i+1}")
    for i in range(2):
        _make_certification(client, entity["id"], f"Inventory Certificate {i+1}")
    for i in range(5):
        _make_asset(client, entity["id"], f"Inventory Asset {i+1}")

    r = client.get(f"/api/v1/entities/{entity['id']}/knowledge-inventory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_id"] == entity["id"]
    assert body["skills"]["count"] == 3
    assert len(body["skills"]["items"]) == 3
    assert body["certifications"]["count"] == 2
    assert len(body["certifications"]["items"]) == 2
    assert body["knowledge_assets"]["count"] == 5
    assert len(body["knowledge_assets"]["items"]) == 5


# --- AT-007 State Vector Event Generation ---
def test_at007_state_vector_event_generation():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Audited Document")

    r = client.get(
        "/api/v1/vault/audit-log",
        params={"event_type": "knowledge_created", "record_id": asset["id"]},
    )
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["record_type"] == "KnowledgeAsset"
    assert entry["entity_id"] == entity["id"]
    assert entry["actor"] is not None


# --- AT-008 OpenAPI Contract ---
def test_at008_openapi_contract():
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    paths = schema["paths"]
    required = [
        "/api/v1/knowledge",
        "/api/v1/knowledge/{asset_id}",
        "/api/v1/knowledge/{asset_id}/lineage",
        "/api/v1/knowledge/{asset_id}/evidence",
        "/api/v1/skills",
        "/api/v1/skills/{skill_id}",
        "/api/v1/certifications",
        "/api/v1/family-records",
        "/api/v1/legacy-assets",
        "/api/v1/entities/{entity_id}/knowledge",
        "/api/v1/entities/{entity_id}/knowledge-inventory",
        "/api/v1/vault/queries/knowledge-no-evidence",
        "/api/v1/vault/queries/skills-no-evidence",
        "/api/v1/vault/queries/certifications-no-evidence",
        "/api/v1/vault/queries/legacy-no-preservation-status",
        "/api/v1/vault/audit-log",
    ]
    for path in required:
        assert path in paths, f"missing OpenAPI path: {path}"
    # Every operation on every path declares responses.
    for path, item in paths.items():
        for method, operation in item.items():
            if method.startswith("x-"):
                continue
            assert "responses" in operation, f"{method} {path} has no responses"
            assert operation["responses"], f"{method} {path} has empty responses"


# --- Lineage (Epic 2.2, Story 2.2.2) ---
def test_lineage_view_and_version_increments():
    client, user_id = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(
        client, entity["id"], "Lineage Document", source="illustrative source"
    )

    r = client.get(f"/api/v1/knowledge/{asset['id']}/lineage")
    assert r.status_code == 200
    lineage = r.json()
    assert lineage["knowledge_asset_id"] == asset["id"]
    assert lineage["created_by"] == user_id
    assert lineage["source"] == "illustrative source"
    assert lineage["version"] == 1
    assert lineage["evidence_count"] == 0
    assert lineage["last_updated"] is not None

    _attach_evidence(client, asset["id"])
    r = client.get(f"/api/v1/knowledge/{asset['id']}/lineage")
    assert r.json()["evidence_count"] == 1
    assert r.json()["version"] == 1

    r = client.put(
        f"/api/v1/knowledge/{asset['id']}",
        json={"title": "Lineage Document v2"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["title"] == "Lineage Document v2"

    r = client.get(f"/api/v1/knowledge/{asset['id']}/lineage")
    assert r.json()["version"] == 2
    assert r.json()["evidence_count"] == 1


def test_lineage_audit():
    """The lineage view reflects the stored audit trail: the
    knowledge_created entry and the asset's creator agree."""
    client, user_id = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Audited Lineage")

    lineage = client.get(f"/api/v1/knowledge/{asset['id']}/lineage").json()
    entries = client.get(
        "/api/v1/vault/audit-log",
        params={"event_type": "knowledge_created", "record_id": asset["id"]},
    ).json()
    assert len(entries) == 1
    assert entries[0]["entity_id"] == entity["id"]
    assert lineage["created_by"] == user_id


# --- Per-model CRUD ---
def test_skill_crud():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(
        client, entity["id"], "CRUD Skill",
        proficiency="advanced", years_experience=5.5,
    )
    assert skill["proficiency"] == "advanced"
    assert skill["years_experience"] == 5.5
    assert skill["verified"] is False

    r = client.get(f"/api/v1/skills/{skill['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "CRUD Skill"

    r = client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"proficiency": "expert", "years_experience": 6.0},
    )
    assert r.status_code == 200
    assert r.json()["proficiency"] == "expert"

    r = client.get("/api/v1/skills", params={"entity_id": entity["id"]})
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.delete(f"/api/v1/skills/{skill['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/v1/skills/{skill['id']}")
    assert r.status_code == 404


def test_certification_crud():
    client, _ = _auth_client()
    entity = _make_entity(client)
    cert = _make_certification(
        client, entity["id"], "CRUD Certificate",
        issuer="Illustrative Institute",
        issue_date="2024-01-15", expiration_date="2027-01-15",
        credential_id="ILL-123",
    )
    assert cert["issuer"] == "Illustrative Institute"
    assert cert["issue_date"] == "2024-01-15"
    assert cert["credential_id"] == "ILL-123"

    r = client.get(f"/api/v1/certifications/{cert['id']}")
    assert r.status_code == 200

    r = client.patch(
        f"/api/v1/certifications/{cert['id']}",
        json={"issuer": "Renamed Institute"},
    )
    assert r.status_code == 200
    assert r.json()["issuer"] == "Renamed Institute"

    r = client.delete(f"/api/v1/certifications/{cert['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/certifications/{cert['id']}").status_code == 404


def test_family_record_crud():
    client, _ = _auth_client()
    entity = _make_entity(client)
    record = _make_family_record(
        client, entity["id"], "CRUD Letter",
        record_type="Letter", description="A teaching letter.",
        date_recorded="1990-05-01",
    )
    assert record["record_type"] == "Letter"
    assert record["date_recorded"] == "1990-05-01"

    r = client.patch(
        f"/api/v1/family-records/{record['id']}",
        json={"title": "Renamed Letter"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed Letter"

    r = client.delete(f"/api/v1/family-records/{record['id']}")
    assert r.status_code == 204


def test_legacy_asset_crud():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_legacy_asset(
        client, entity["id"], "CRUD Patent",
        asset_type="Patent", estimated_impact=42.0,
    )
    assert asset["asset_type"] == "Patent"
    assert asset["estimated_impact"] == 42.0
    assert asset["preservation_status"] is None

    r = client.get(f"/api/v1/legacy-assets/{asset['id']}")
    assert r.status_code == 200

    r = client.delete(f"/api/v1/legacy-assets/{asset['id']}")
    assert r.status_code == 204


def test_knowledge_asset_delete_cascades_evidence():
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Doomed Document")
    _attach_evidence(client, asset["id"])
    assert len(client.get(f"/api/v1/knowledge/{asset['id']}/evidence").json()) == 1

    r = client.delete(f"/api/v1/knowledge/{asset['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/knowledge/{asset['id']}").status_code == 404
    assert client.get(f"/api/v1/knowledge/{asset['id']}/evidence").status_code == 404


def test_skill_asset_link_survives_asset_delete():
    """Deleting a skill's KnowledgeAsset clears the link (SET NULL)
    instead of deleting the skill."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    asset = _make_asset(client, entity["id"], "Linked Skill Asset", category="Skill")
    skill = _make_skill(
        client, entity["id"], "Linked Skill", knowledge_asset_id=asset["id"]
    )
    assert skill["knowledge_asset_id"] == asset["id"]

    client.delete(f"/api/v1/knowledge/{asset['id']}")
    r = client.get(f"/api/v1/skills/{skill['id']}")
    assert r.status_code == 200
    assert r.json()["knowledge_asset_id"] is None


# --- Audit events on creation ---
def test_creation_events_written():
    client, _ = _auth_client()
    entity = _make_entity(client)
    skill = _make_skill(client, entity["id"], "Event Skill")
    cert = _make_certification(client, entity["id"], "Event Certificate")
    legacy = _make_legacy_asset(client, entity["id"], "Event Book")

    for event_type, record_type, record_id in (
        ("skill_added", "Skill", skill["id"]),
        ("certification_added", "Certification", cert["id"]),
        ("legacy_created", "LegacyAsset", legacy["id"]),
    ):
        r = client.get(
            "/api/v1/vault/audit-log",
            params={"event_type": event_type, "record_id": record_id},
        )
        assert r.status_code == 200
        entries = r.json()
        assert len(entries) == 1, (event_type, entries)
        assert entries[0]["record_type"] == record_type
        assert entries[0]["entity_id"] == entity["id"]


def test_family_creation_audit_has_no_named_event():
    """FamilyRecord creation is written to the audit log, but no
    family event name was invented: the entry carries event_type
    NULL and the record_type / record_id carry the record."""
    client, _ = _auth_client()
    entity = _make_entity(client)
    record = _make_family_record(client, entity["id"], "Audited Biography")

    r = client.get(
        "/api/v1/vault/audit-log",
        params={"record_type": "FamilyRecord", "record_id": record["id"]},
    )
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["event_type"] is None
    assert entries[0]["entity_id"] == entity["id"]


# --- The four Sprint 2 missing-edge queries ---
def test_query_knowledge_no_evidence():
    client, _ = _auth_client()
    entity_a = _make_entity(client, name="Query Entity A")
    entity_b = _make_entity(client, name="Query Entity B")
    with_evidence = _make_asset(client, entity_a["id"], "With Evidence")
    _attach_evidence(client, with_evidence["id"])
    without_a = _make_asset(client, entity_a["id"], "Without Evidence A")
    without_b = _make_asset(client, entity_b["id"], "Without Evidence B")

    r = client.get("/api/v1/vault/queries/knowledge-no-evidence")
    assert r.status_code == 200
    titles = {row["title"] for row in r.json()}
    assert titles == {"Without Evidence A", "Without Evidence B"}

    r = client.get(
        "/api/v1/vault/queries/knowledge-no-evidence",
        params={"entity_id": entity_a["id"]},
    )
    assert {row["title"] for row in r.json()} == {"Without Evidence A"}
    assert without_a["id"] in {row["id"] for row in r.json()}


def test_query_skills_no_evidence():
    client, _ = _auth_client()
    entity = _make_entity(client)
    evidenced_asset = _make_asset(
        client, entity["id"], "Evidenced Skill Asset", category="Skill"
    )
    _attach_evidence(client, evidenced_asset["id"])
    unevidenced_asset = _make_asset(
        client, entity["id"], "Unevidenced Skill Asset", category="Skill"
    )
    evidenced = _make_skill(
        client, entity["id"], "Evidenced Skill",
        knowledge_asset_id=evidenced_asset["id"],
    )
    linked_no_evidence = _make_skill(
        client, entity["id"], "Linked No-Evidence Skill",
        knowledge_asset_id=unevidenced_asset["id"],
    )
    unlinked = _make_skill(client, entity["id"], "Unlinked Skill")

    r = client.get("/api/v1/vault/queries/skills-no-evidence")
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert names == {"Linked No-Evidence Skill", "Unlinked Skill"}
    assert evidenced["name"] not in names


def test_query_certifications_no_evidence():
    client, _ = _auth_client()
    entity = _make_entity(client)
    evidenced_asset = _make_asset(
        client, entity["id"], "Evidenced Certificate", category="Certification"
    )
    _attach_evidence(client, evidenced_asset["id"])
    unevidenced_asset = _make_asset(
        client, entity["id"], "Unevidenced Certificate", category="Certification"
    )
    _make_certification(client, entity["id"], "Evidenced Certificate")
    _make_certification(client, entity["id"], "Unevidenced Certificate")
    _make_certification(client, entity["id"], "Orphan Certificate")

    r = client.get("/api/v1/vault/queries/certifications-no-evidence")
    assert r.status_code == 200
    titles = {row["title"] for row in r.json()}
    assert titles == {"Unevidenced Certificate", "Orphan Certificate"}


def test_query_legacy_no_preservation_status():
    client, _ = _auth_client()
    entity = _make_entity(client)
    preserved = _make_legacy_asset(
        client, entity["id"], "Preserved Book",
        preservation_status="preserved",
    )
    unset = _make_legacy_asset(client, entity["id"], "Unset Book")

    r = client.get("/api/v1/vault/queries/legacy-no-preservation-status")
    assert r.status_code == 200
    titles = {row["title"] for row in r.json()}
    assert titles == {"Unset Book"}
    assert preserved["title"] not in titles


# --- Validation and access control ---
def test_vault_invalid_enum_rejected():
    client, _ = _auth_client()
    entity = _make_entity(client)
    r = client.post(
        "/api/v1/knowledge",
        json={"entity_id": entity["id"], "title": "Nope", "category": "Nation"},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/v1/family-records",
        json={"entity_id": entity["id"], "title": "Nope", "record_type": "Epic"},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/v1/legacy-assets",
        json={"entity_id": entity["id"], "title": "Nope", "asset_type": "Crypto"},
    )
    assert r.status_code == 422


def test_vault_unknown_entity_404():
    client, _ = _auth_client()
    r = client.post(
        "/api/v1/knowledge",
        json={"entity_id": 999999, "title": "Nope", "category": "Document"},
    )
    assert r.status_code == 404
    r = client.post(
        "/api/v1/skills", json={"entity_id": 999999, "name": "Nope"}
    )
    assert r.status_code == 404


def test_vault_unknown_asset_or_record_404():
    client, _ = _auth_client()
    assert client.get("/api/v1/knowledge/999999").status_code == 404
    assert client.get("/api/v1/skills/999999").status_code == 404
    assert client.get("/api/v1/knowledge/999999/lineage").status_code == 404
    r = client.post(
        "/api/v1/knowledge/999999/evidence",
        json={"source": "x", "source_type": "Document", "confidence": 0.5},
    )
    assert r.status_code == 404


def test_vault_unknown_knowledge_asset_link_404():
    client, _ = _auth_client()
    entity = _make_entity(client)
    r = client.post(
        "/api/v1/skills",
        json={
            "entity_id": entity["id"],
            "name": "Bad Link Skill",
            "knowledge_asset_id": 999999,
        },
    )
    assert r.status_code == 404


def test_vault_access_control():
    client_a, _ = _auth_client()
    client_b, _ = _auth_client()
    entity = _make_entity(client_a, name="Private Vault Entity")
    asset = _make_asset(client_a, entity["id"], "Private Document")

    # Another user cannot read the asset, its lineage, or its evidence.
    assert client_b.get(f"/api/v1/knowledge/{asset['id']}").status_code == 403
    assert client_b.get(f"/api/v1/knowledge/{asset['id']}/lineage").status_code == 403
    assert client_b.get(f"/api/v1/knowledge/{asset['id']}/evidence").status_code == 403
    # Cannot create records on it or attach evidence to it.
    r = client_b.post(
        "/api/v1/skills", json={"entity_id": entity["id"], "name": "Hacked"}
    )
    assert r.status_code == 403
    r = client_b.post(
        f"/api/v1/knowledge/{asset['id']}/evidence",
        json={"source": "x", "source_type": "Document", "confidence": 0.5},
    )
    assert r.status_code == 403
    # Cannot see it in their lists.
    r = client_b.get(f"/api/v1/entities/{entity['id']}/knowledge")
    assert r.status_code == 403


def test_vault_requires_auth():
    client = TestClient(app)
    assert client.get("/api/v1/skills").status_code == 401
    assert client.get("/api/v1/vault/queries/skills-no-evidence").status_code == 401
    assert client.post(
        "/api/v1/knowledge",
        json={"entity_id": 1, "title": "Nope", "category": "Document"},
    ).status_code == 401


def test_entity_knowledge_list_pagination():
    client, _ = _auth_client()
    entity = _make_entity(client)
    for i in range(3):
        _make_asset(client, entity["id"], f"Paged Asset {i+1}")

    r = client.get(
        f"/api/v1/entities/{entity['id']}/knowledge",
        params={"limit": 2, "offset": 0},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2
    r = client.get(
        f"/api/v1/entities/{entity['id']}/knowledge",
        params={"limit": 2, "offset": 2},
    )
    assert len(r.json()) == 1
