"""Knowledge Vault endpoints — Engine v1 Sprint 2 (formal definition).

POST   /api/v1/knowledge                          create a knowledge asset
GET    /api/v1/knowledge/{id}                     one asset
PUT    /api/v1/knowledge/{id}                     update (version increments)
DELETE /api/v1/knowledge/{id}                     delete the asset
GET    /api/v1/entities/{id}/knowledge            an entity's assets, paginated
GET    /api/v1/knowledge/{id}/lineage             lineage view (Epic 2.2)
POST   /api/v1/knowledge/{id}/evidence            attach evidence
GET    /api/v1/knowledge/{id}/evidence            list an asset's evidence
POST   /api/v1/skills  (+ GET /{id}, PATCH, DELETE, list, entity list)
POST   /api/v1/certifications  (same shape)
POST   /api/v1/family-records  (same shape)
POST   /api/v1/legacy-assets  (same shape)
GET    /api/v1/entities/{id}/knowledge-inventory  counts + lists (AT-006)
GET    /api/v1/vault/queries/knowledge-no-evidence
GET    /api/v1/vault/queries/skills-no-evidence
GET    /api/v1/vault/queries/certifications-no-evidence
GET    /api/v1/vault/queries/legacy-no-preservation-status
GET    /api/v1/vault/audit-log                    audit entries (Epic 2.8)

All endpoints require auth. Records belong to an entity; writes
require owning that entity, reads require access to it (the Sprint 1
ownership rule, unchanged).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
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
from app.schemas.vault import (
    AuditLogOut,
    CertificationCreate,
    CertificationOut,
    CertificationUpdate,
    EvidenceCreate,
    EvidenceOut,
    FamilyRecordCreate,
    FamilyRecordOut,
    FamilyRecordUpdate,
    KnowledgeAssetCreate,
    KnowledgeAssetOut,
    KnowledgeAssetUpdate,
    KnowledgeInventoryOut,
    KnowledgeLineageOut,
    LegacyAssetCreate,
    LegacyAssetOut,
    LegacyAssetUpdate,
    SkillCreate,
    SkillOut,
    SkillUpdate,
)
from app.services.vault_service import (
    EVENT_CERTIFICATION_ADDED,
    EVENT_KNOWLEDGE_CREATED,
    EVENT_LEGACY_CREATED,
    EVENT_LEGACY_PRESERVATION_UPDATED,
    EVENT_SKILL_ADDED,
    _apply_patch,
    accessible_entity_ids,
    assert_record_access,
    assert_record_owner,
    evidence_count,
    get_record_or_404,
    log_event,
    query_certifications_no_evidence,
    query_knowledge_no_evidence,
    query_legacy_no_preservation_status,
    query_skills_no_evidence,
    resolve_entity_for_read,
    resolve_entity_for_write,
)

router = APIRouter(prefix="/api/v1", tags=["vault"])


# --- Shared generic helpers (per-registry CRUD) ---

async def _create_row(
    db: AsyncSession,
    model,
    payload,
    *,
    user: User,
    event_type: str | None,
    record_type: str,
    log: bool = True,
):
    """Create a registry row on an owned entity and write its audit
    entry. `event_type` may be None (family creation: the audit
    entry is written with no named event type, per the Sprint 2
    definition)."""
    entity = await resolve_entity_for_write(db, payload.entity_id, user)
    data = payload.model_dump()
    if data.get("knowledge_asset_id") is not None:
        await get_record_or_404(db, KnowledgeAsset, data["knowledge_asset_id"])
    row = model(**data)
    db.add(row)
    await db.flush()
    if log:
        log_event(
            db,
            event_type=event_type,
            entity_id=entity.id,
            record_type=record_type,
            record_id=row.id,
            actor=user.email,
            details={"title": getattr(row, "title", None) or getattr(row, "name", None)},
        )
    await db.commit()
    await db.refresh(row)
    return row


async def _get_row(db: AsyncSession, model, row_id: int, user: User):
    row = await get_record_or_404(db, model, row_id)
    await assert_record_access(db, row, user)
    return row


async def _list_rows(
    db: AsyncSession,
    model,
    user: User,
    entity_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List rows on entities the caller may read, optionally
    filtered to one entity."""
    ids = await accessible_entity_ids(db, user)
    stmt = (
        select(model)
        .where(model.entity_id.in_(ids))
        .order_by(model.created_at, model.id)
        .limit(limit)
        .offset(offset)
    )
    if entity_id is not None:
        await resolve_entity_for_read(db, entity_id, user)
        stmt = stmt.where(model.entity_id == entity_id)
    return list((await db.execute(stmt)).scalars().all())


async def _patch_row(db: AsyncSession, model, row_id: int, payload, user: User):
    row = await get_record_or_404(db, model, row_id)
    await assert_record_owner(db, row, user)
    data = payload.model_dump(exclude_unset=True)
    if data.get("knowledge_asset_id") is not None:
        await get_record_or_404(db, KnowledgeAsset, data["knowledge_asset_id"])
    _apply_patch(row, data)
    await db.commit()
    await db.refresh(row)
    return row


async def _delete_row(db: AsyncSession, model, row_id: int, user: User):
    row = await get_record_or_404(db, model, row_id)
    await assert_record_owner(db, row, user)
    await db.delete(row)
    await db.commit()


# --- KnowledgeAsset (the unified vault record) ---

@router.post(
    "/knowledge",
    response_model=KnowledgeAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_asset(
    payload: KnowledgeAssetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeAsset:
    """Create a KnowledgeAsset. The caller becomes its creator
    (created_by), version starts at 1, and a knowledge_created
    audit entry is written (AT-007)."""
    entity = await resolve_entity_for_write(db, payload.entity_id, current_user)
    asset = KnowledgeAsset(
        **payload.model_dump(),
        created_by=current_user.id,
        version=1,
    )
    db.add(asset)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_KNOWLEDGE_CREATED,
        entity_id=entity.id,
        record_type="KnowledgeAsset",
        record_id=asset.id,
        actor=current_user.email,
        details={"title": asset.title, "category": asset.category},
    )
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/knowledge/{asset_id}", response_model=KnowledgeAssetOut)
async def get_knowledge_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeAsset:
    """One KnowledgeAsset (AT-001: retrieve returns the stored
    asset)."""
    return await _get_row(db, KnowledgeAsset, asset_id, current_user)


@router.put("/knowledge/{asset_id}", response_model=KnowledgeAssetOut)
async def update_knowledge_asset(
    asset_id: int,
    payload: KnowledgeAssetUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeAsset:
    """Update a KnowledgeAsset. Version increments on every
    successful update."""
    asset = await get_record_or_404(db, KnowledgeAsset, asset_id)
    await assert_record_owner(db, asset, current_user)
    _apply_patch(asset, payload.model_dump(exclude_unset=True))
    asset.version += 1
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/knowledge/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_knowledge_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a KnowledgeAsset. Its EvidenceRecords are deleted
    with it (CASCADE); Skills linked to it keep their row and lose
    the link (SET NULL)."""
    await _delete_row(db, KnowledgeAsset, asset_id, current_user)


@router.get(
    "/entities/{entity_id}/knowledge",
    response_model=list[KnowledgeAssetOut],
)
async def list_entity_knowledge(
    entity_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeAsset]:
    """An entity's KnowledgeAssets, paginated."""
    await resolve_entity_for_read(db, entity_id, current_user)
    return await _list_rows(
        db, KnowledgeAsset, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/knowledge/{asset_id}/lineage", response_model=KnowledgeLineageOut
)
async def get_knowledge_lineage(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeLineageOut:
    """The lineage view (Epic 2.2, Story 2.2.2): created_by,
    source, version, last_updated, and the computed evidence_count."""
    asset = await _get_row(db, KnowledgeAsset, asset_id, current_user)
    return KnowledgeLineageOut(
        knowledge_asset_id=asset.id,
        created_by=asset.created_by,
        source=asset.source,
        version=asset.version,
        last_updated=asset.updated_at,
        evidence_count=await evidence_count(db, asset.id),
    )


# --- Evidence (attached to a KnowledgeAsset) ---

@router.post(
    "/knowledge/{asset_id}/evidence",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def attach_evidence(
    asset_id: int,
    payload: EvidenceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EvidenceRecord:
    """Attach one EvidenceRecord to a KnowledgeAsset (AT-002:
    attaching evidence increases the asset's evidence_count)."""
    asset = await get_record_or_404(db, KnowledgeAsset, asset_id)
    await assert_record_owner(db, asset, current_user)
    record = EvidenceRecord(
        knowledge_asset_id=asset.id, **payload.model_dump()
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.get(
    "/knowledge/{asset_id}/evidence", response_model=list[EvidenceOut]
)
async def list_evidence(
    asset_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EvidenceRecord]:
    """Every EvidenceRecord attached to one KnowledgeAsset."""
    asset = await _get_row(db, KnowledgeAsset, asset_id, current_user)
    result = await db.execute(
        select(EvidenceRecord)
        .where(EvidenceRecord.knowledge_asset_id == asset.id)
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


# --- Skills ---

@router.post(
    "/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED
)
async def create_skill(
    payload: SkillCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Skill:
    """Create a Skill. Set knowledge_asset_id to link it to its
    KnowledgeAsset representation, so evidence attaches through
    the asset."""
    return await _create_row(
        db, Skill, payload,
        user=current_user,
        event_type=EVENT_SKILL_ADDED, record_type="Skill",
    )


@router.get("/skills/{skill_id}", response_model=SkillOut)
async def get_skill(
    skill_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Skill:
    return await _get_row(db, Skill, skill_id, current_user)


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(
    entity_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Skill]:
    return await _list_rows(
        db, Skill, current_user, entity_id=entity_id, limit=limit, offset=offset
    )


@router.get(
    "/entities/{entity_id}/skills", response_model=list[SkillOut]
)
async def list_entity_skills(
    entity_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Skill]:
    await resolve_entity_for_read(db, entity_id, current_user)
    return await _list_rows(
        db, Skill, current_user, entity_id=entity_id, limit=limit, offset=offset
    )


@router.patch("/skills/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: int,
    payload: SkillUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Skill:
    """Update a Skill — including marking verified=true (AT-003)."""
    return await _patch_row(db, Skill, skill_id, payload, current_user)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_skill(
    skill_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _delete_row(db, Skill, skill_id, current_user)


# --- Certifications ---

@router.post(
    "/certifications",
    response_model=CertificationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_certification(
    payload: CertificationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Certification:
    return await _create_row(
        db, Certification, payload,
        user=current_user,
        event_type=EVENT_CERTIFICATION_ADDED, record_type="Certification",
    )


@router.get(
    "/certifications/{certification_id}", response_model=CertificationOut
)
async def get_certification(
    certification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Certification:
    return await _get_row(db, Certification, certification_id, current_user)


@router.get("/certifications", response_model=list[CertificationOut])
async def list_certifications(
    entity_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Certification]:
    return await _list_rows(
        db, Certification, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/entities/{entity_id}/certifications",
    response_model=list[CertificationOut],
)
async def list_entity_certifications(
    entity_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Certification]:
    await resolve_entity_for_read(db, entity_id, current_user)
    return await _list_rows(
        db, Certification, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.patch(
    "/certifications/{certification_id}", response_model=CertificationOut
)
async def update_certification(
    certification_id: int,
    payload: CertificationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Certification:
    return await _patch_row(
        db, Certification, certification_id, payload, current_user
    )


@router.delete(
    "/certifications/{certification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_certification(
    certification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _delete_row(db, Certification, certification_id, current_user)


# --- Family records ---

@router.post(
    "/family-records",
    response_model=FamilyRecordOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_family_record(
    payload: FamilyRecordCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FamilyRecord:
    """Create a FamilyRecord. Its creation is written to the audit
    log with no named event type (event_type NULL) — no family
    event name was invented, per the Sprint 2 definition."""
    return await _create_row(
        db, FamilyRecord, payload,
        user=current_user,
        event_type=None, record_type="FamilyRecord",
    )


@router.get(
    "/family-records/{record_id}", response_model=FamilyRecordOut
)
async def get_family_record(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FamilyRecord:
    return await _get_row(db, FamilyRecord, record_id, current_user)


@router.get("/family-records", response_model=list[FamilyRecordOut])
async def list_family_records(
    entity_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FamilyRecord]:
    return await _list_rows(
        db, FamilyRecord, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/entities/{entity_id}/family-records",
    response_model=list[FamilyRecordOut],
)
async def list_entity_family_records(
    entity_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FamilyRecord]:
    """An entity's family records, paginated (AT-005)."""
    await resolve_entity_for_read(db, entity_id, current_user)
    return await _list_rows(
        db, FamilyRecord, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.patch(
    "/family-records/{record_id}", response_model=FamilyRecordOut
)
async def update_family_record(
    record_id: int,
    payload: FamilyRecordUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FamilyRecord:
    return await _patch_row(
        db, FamilyRecord, record_id, payload, current_user
    )


@router.delete(
    "/family-records/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_family_record(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _delete_row(db, FamilyRecord, record_id, current_user)


# --- Legacy assets ---

@router.post(
    "/legacy-assets",
    response_model=LegacyAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_legacy_asset(
    payload: LegacyAssetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegacyAsset:
    return await _create_row(
        db, LegacyAsset, payload,
        user=current_user,
        event_type=EVENT_LEGACY_CREATED, record_type="LegacyAsset",
    )


@router.get("/legacy-assets/{asset_id}", response_model=LegacyAssetOut)
async def get_legacy_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegacyAsset:
    return await _get_row(db, LegacyAsset, asset_id, current_user)


@router.get("/legacy-assets", response_model=list[LegacyAssetOut])
async def list_legacy_assets(
    entity_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LegacyAsset]:
    return await _list_rows(
        db, LegacyAsset, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/entities/{entity_id}/legacy-assets",
    response_model=list[LegacyAssetOut],
)
async def list_entity_legacy_assets(
    entity_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LegacyAsset]:
    await resolve_entity_for_read(db, entity_id, current_user)
    return await _list_rows(
        db, LegacyAsset, current_user, entity_id=entity_id,
        limit=limit, offset=offset,
    )


@router.patch("/legacy-assets/{asset_id}", response_model=LegacyAssetOut)
async def update_legacy_asset(
    asset_id: int,
    payload: LegacyAssetUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegacyAsset:
    """Update a LegacyAsset. A change to preservation_status writes
    a legacy_preservation_updated audit entry (AT-004)."""
    asset = await get_record_or_404(db, LegacyAsset, asset_id)
    await assert_record_owner(db, asset, current_user)
    old_status = asset.preservation_status
    data = payload.model_dump(exclude_unset=True)
    _apply_patch(asset, data)
    if (
        "preservation_status" in data
        and data["preservation_status"] is not None
        and data["preservation_status"] != old_status
    ):
        log_event(
            db,
            event_type=EVENT_LEGACY_PRESERVATION_UPDATED,
            entity_id=asset.entity_id,
            record_type="LegacyAsset",
            record_id=asset.id,
            actor=current_user.email,
            details={
                "old_preservation_status": old_status,
                "new_preservation_status": data["preservation_status"],
            },
        )
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete(
    "/legacy-assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_legacy_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _delete_row(db, LegacyAsset, asset_id, current_user)


# --- Knowledge inventory (AT-006) ---

@router.get(
    "/entities/{entity_id}/knowledge-inventory",
    response_model=KnowledgeInventoryOut,
)
async def get_knowledge_inventory(
    entity_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeInventoryOut:
    """Counts and lists of an entity's skills, certifications, and
    knowledge assets (AT-006)."""
    entity = await resolve_entity_for_read(db, entity_id, current_user)

    async def _count(model) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(model)
            .where(model.entity_id == entity.id)
        )
        return int(result.scalar_one())

    skills = await _list_rows(
        db, Skill, current_user, entity_id=entity.id, limit=limit
    )
    certifications = await _list_rows(
        db, Certification, current_user, entity_id=entity.id, limit=limit
    )
    assets = await _list_rows(
        db, KnowledgeAsset, current_user, entity_id=entity.id, limit=limit
    )
    return KnowledgeInventoryOut(
        entity_id=entity.id,
        skills={"count": await _count(Skill), "items": skills},
        certifications={
            "count": await _count(Certification),
            "items": certifications,
        },
        knowledge_assets={
            "count": await _count(KnowledgeAsset),
            "items": assets,
        },
    )


# --- Missing-edge queries (Sprint 2 equivalents) ---

@router.get(
    "/vault/queries/knowledge-no-evidence",
    response_model=list[KnowledgeAssetOut],
)
async def get_knowledge_no_evidence(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeAsset]:
    """Knowledge assets with no supporting evidence."""
    return await query_knowledge_no_evidence(db, current_user, entity_id)


@router.get(
    "/vault/queries/skills-no-evidence", response_model=list[SkillOut]
)
async def get_skills_no_evidence(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Skill]:
    """Skills with no evidence (no linked asset, or a linked asset
    with no evidence)."""
    return await query_skills_no_evidence(db, current_user, entity_id)


@router.get(
    "/vault/queries/certifications-no-evidence",
    response_model=list[CertificationOut],
)
async def get_certifications_no_evidence(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Certification]:
    """Certifications with no evidence (matched by entity +
    category "Certification" + title — see the service docstring)."""
    return await query_certifications_no_evidence(db, current_user, entity_id)


@router.get(
    "/vault/queries/legacy-no-preservation-status",
    response_model=list[LegacyAssetOut],
)
async def get_legacy_no_preservation_status(
    entity_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LegacyAsset]:
    """Legacy assets whose preservation status was never set."""
    return await query_legacy_no_preservation_status(
        db, current_user, entity_id
    )


# --- Audit log (Epic 2.8) ---

@router.get("/vault/audit-log", response_model=list[AuditLogOut])
async def list_audit_log(
    entity_id: int | None = Query(default=None),
    record_type: str | None = Query(default=None),
    record_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLog]:
    """Audit entries, newest last, scoped to entities the caller
    may read (entries with no entity are visible to any
    authenticated user)."""
    ids = await accessible_entity_ids(db, current_user)
    stmt = (
        select(AuditLog)
        .where(
            or_(
                AuditLog.entity_id.in_(ids),
                AuditLog.entity_id.is_(None),
            )
        )
        .order_by(AuditLog.timestamp, AuditLog.id)
        .limit(limit)
        .offset(offset)
    )
    if entity_id is not None:
        await resolve_entity_for_read(db, entity_id, current_user)
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if record_type is not None:
        stmt = stmt.where(AuditLog.record_type == record_type)
    if record_id is not None:
        stmt = stmt.where(AuditLog.record_id == record_id)
    if event_type is not None:
        stmt = stmt.where(AuditLog.event_type == event_type)
    return list((await db.execute(stmt)).scalars().all())
