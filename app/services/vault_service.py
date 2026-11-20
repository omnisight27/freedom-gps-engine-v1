"""Knowledge Vault service — record lookup, ownership, audit events,
and the missing-edge queries.

Ownership rule (Sprint 1, unchanged): an entity's owner is its
creator. Records belong to an entity; writes require owning that
entity, reads require access to it. Evidence follows the same rule
through its KnowledgeAsset's entity.

Named creation events (Epic 2.8): knowledge_created,
skill_added, certification_added, legacy_created. FamilyRecord
creation is also written to the audit log, with no named event type
(event_type NULL) — no family event name was invented, per the
Sprint 2 definition. A LegacyAsset preservation_status change
writes a legacy_preservation_updated entry (AT-004).

The four Sprint 2 queries are the ones answerable with this
spec's models: knowledge assets with no evidence, skills with no
evidence, certifications with no evidence, and legacy assets with
no preservation status. The five original queries (revenue-linked
skills, opportunity-linked knowledge, preservation-plan family
records, utilized certifications, graph-linked documents) need
opportunity links, revenue links, utilization, and graph
relationships that live in Sprint 3 (graph) and Sprint 4
(opportunity) — they are not smuggled in here.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity import Entity
from app.models.user import User
from app.models.vault import (
    AuditLog,
    Certification,
    EvidenceRecord,
    FamilyRecord,
    KnowledgeAsset,
    LegacyAsset,
    Skill,
)
from app.services.entity_service import (
    assert_entity_access,
    assert_entity_owner,
    get_entity_or_404,
)

# Named creation events (Epic 2.8).
EVENT_KNOWLEDGE_CREATED = "knowledge_created"
EVENT_SKILL_ADDED = "skill_added"
EVENT_CERTIFICATION_ADDED = "certification_added"
EVENT_LEGACY_CREATED = "legacy_created"
# Written when a LegacyAsset's preservation_status changes (AT-004).
EVENT_LEGACY_PRESERVATION_UPDATED = "legacy_preservation_updated"


async def get_record_or_404(db: AsyncSession, model, record_id: int):
    """Load a Vault record by id or raise 404."""
    result = await db.execute(select(model).where(model.id == record_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{model.__name__} not found",
        )
    return row


async def accessible_entity_ids(db: AsyncSession, user: User) -> set[int]:
    """Ids of entities the caller may read: their own plus shared
    (unowned) ones — the Sprint 1 list rule."""
    result = await db.execute(
        select(Entity.id).where(
            or_(Entity.owner_id == user.id, Entity.owner_id.is_(None))
        )
    )
    return set(result.scalars().all())


async def resolve_entity_for_write(
    db: AsyncSession, entity_id: int, user: User
) -> Entity:
    """Load an entity for a write: 404 when unknown, 403 unless the
    caller owns it."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_owner(entity, user)
    return entity


async def resolve_entity_for_read(
    db: AsyncSession, entity_id: int, user: User
) -> Entity:
    """Load an entity for a read: 404 when unknown, 403 unless the
    caller may access it."""
    entity = await get_entity_or_404(db, entity_id)
    assert_entity_access(entity, user)
    return entity


async def assert_record_access(db: AsyncSession, row, user: User) -> None:
    """Raise 403 unless the caller may read the record's entity."""
    entity = await get_entity_or_404(db, row.entity_id)
    assert_entity_access(entity, user)


async def assert_record_owner(db: AsyncSession, row, user: User) -> None:
    """Raise 403 unless the caller owns the record's entity."""
    entity = await get_entity_or_404(db, row.entity_id)
    assert_entity_owner(entity, user)


def _apply_patch(row, data: dict) -> None:
    """Apply a PATCH/PUT payload: only supplied, non-None values
    change. (None means "no change", matching the entities router:
    a client cannot clear a field through PATCH in v1.)"""
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value)


def log_event(
    db: AsyncSession,
    *,
    event_type: str | None,
    entity_id: int | None,
    record_type: str | None,
    record_id: int | None,
    actor: str | None,
    details: dict | None = None,
) -> AuditLog:
    """Append one audit entry to the session (the caller commits).
    `details` is stored as a JSON string."""
    entry = AuditLog(
        event_type=event_type,
        entity_id=entity_id,
        record_type=record_type,
        record_id=record_id,
        actor=actor,
        details=json.dumps(details) if details is not None else None,
    )
    db.add(entry)
    return entry


async def evidence_count(db: AsyncSession, asset_id: int) -> int:
    """How many EvidenceRecords support one KnowledgeAsset."""
    result = await db.execute(
        select(func.count())
        .select_from(EvidenceRecord)
        .where(EvidenceRecord.knowledge_asset_id == asset_id)
    )
    return int(result.scalar_one())


# --- The four Sprint 2 missing-edge queries ---

async def query_knowledge_no_evidence(
    db: AsyncSession, user: User, entity_id: int | None = None
) -> list[KnowledgeAsset]:
    """Knowledge assets with no supporting evidence at all."""
    ids = await accessible_entity_ids(db, user)
    stmt = (
        select(KnowledgeAsset)
        .where(KnowledgeAsset.entity_id.in_(ids))
        .where(
            ~exists().where(
                EvidenceRecord.knowledge_asset_id == KnowledgeAsset.id
            )
        )
        .order_by(KnowledgeAsset.created_at, KnowledgeAsset.id)
    )
    if entity_id is not None:
        await resolve_entity_for_read(db, entity_id, user)
        stmt = stmt.where(KnowledgeAsset.entity_id == entity_id)
    return list((await db.execute(stmt)).scalars().all())


async def query_skills_no_evidence(
    db: AsyncSession, user: User, entity_id: int | None = None
) -> list[Skill]:
    """Skills with no evidence: no linked KnowledgeAsset, or a
    linked asset that itself holds no EvidenceRecords. Evidence
    attaches to a skill only through its asset."""
    ids = await accessible_entity_ids(db, user)
    stmt = (
        select(Skill)
        .where(Skill.entity_id.in_(ids))
        .where(
            or_(
                Skill.knowledge_asset_id.is_(None),
                ~exists().where(
                    EvidenceRecord.knowledge_asset_id
                    == Skill.knowledge_asset_id
                ),
            )
        )
        .order_by(Skill.created_at, Skill.id)
    )
    if entity_id is not None:
        await resolve_entity_for_read(db, entity_id, user)
        stmt = stmt.where(Skill.entity_id == entity_id)
    return list((await db.execute(stmt)).scalars().all())


async def query_certifications_no_evidence(
    db: AsyncSession, user: User, entity_id: int | None = None
) -> list[Certification]:
    """Certifications with no evidence.

    Sprint 3: a certification is evidenced through its real
    asset link (knowledge_asset_id) — the same pattern Skill has
    had since Sprint 2. The title match is kept as a fallback
    for certifications created before the link existed (same
    entity + category "Certification" + same title, with
    evidence): removing it would change the results for those
    older records. The graph query (/graph/queries/...) uses the
    link only — it is the canonical implementation going forward.
    """
    ids = await accessible_entity_ids(db, user)
    evidenced = (
        select(KnowledgeAsset.id)
        .where(
            or_(
                KnowledgeAsset.id == Certification.knowledge_asset_id,
                and_(
                    KnowledgeAsset.entity_id == Certification.entity_id,
                    KnowledgeAsset.category == "Certification",
                    KnowledgeAsset.title == Certification.title,
                ),
            )
        )
        .where(
            exists().where(
                EvidenceRecord.knowledge_asset_id == KnowledgeAsset.id
            )
        )
    )
    stmt = (
        select(Certification)
        .where(Certification.entity_id.in_(ids))
        .where(~exists(evidenced))
        .order_by(Certification.created_at, Certification.id)
    )
    if entity_id is not None:
        await resolve_entity_for_read(db, entity_id, user)
        stmt = stmt.where(Certification.entity_id == entity_id)
    return list((await db.execute(stmt)).scalars().all())


async def query_legacy_no_preservation_status(
    db: AsyncSession, user: User, entity_id: int | None = None
) -> list[LegacyAsset]:
    """Legacy assets whose preservation status was never set."""
    ids = await accessible_entity_ids(db, user)
    stmt = select(LegacyAsset).where(
        LegacyAsset.entity_id.in_(ids),
        or_(
            LegacyAsset.preservation_status.is_(None),
            LegacyAsset.preservation_status == "",
        ),
    )
    if entity_id is not None:
        await resolve_entity_for_read(db, entity_id, user)
        stmt = stmt.where(LegacyAsset.entity_id == entity_id)
    stmt = stmt.order_by(LegacyAsset.created_at, LegacyAsset.id)
    return list((await db.execute(stmt)).scalars().all())
