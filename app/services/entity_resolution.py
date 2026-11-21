"""Entity Resolution — Phase C.1 (EO 007A).

The graph cannot discover opportunities until it knows that
multiple representations of the same real-world thing are one
entity: One Entity = One Identity = One Node.

The engine has two parts:

1. The resolver — `resolve_or_create`. Every name that enters
   the system (an API create, an import's funder or agency) is
   normalized and matched against the entities the caller can
   see, in layers:
     L1 Exact Match — the incoming spelling equals an entity's
        canonical_name or name exactly (case-sensitive).
        Confidence 1.00. Auto-attach.
     L2 Normalized Match — normalize_name(input) equals the
        normalized canonical key of an entity (case, accents,
        punctuation, spacing, and legal-form suffixes ignored).
        Confidence 0.98. Auto-attach.
     L3 Alias Match — normalize_name(input) equals the
        normalized key of one of an entity's stored aliases
        (e.g. "Nick Long" for "Nicholas Long"). Confidence 0.95.
        Auto-attach.
     L4 Context Match — only what the feeds actually contain:
        domain equality (the host of a grant's URL) or, for
        contracts, same state plus a shared token. Confidence
        0.70-0.89. QUEUE ONLY — a new entity is created for the
        incoming record and a review row proposes the merge.
        Never auto-attaches.
     L5 Human Review Queue — the review row an L4 candidate
        creates; the operator approves (merge) or rejects
        (the pair is suppressed from future re-queuing).
   L1-L3 auto-attach and record the incoming spelling as an
   alias when it differs from the canonical name.

   Layer note (read before changing the ladder): L2 matches
   against canonical keys only; L3 matches against alias keys.
   The distinction is what the Canon's examples need: "NICNACS"
   is a case variant of the canonical "NicNacs", so it matches
   at L2 (0.98); "Nick Long" is a genuinely different spelling
   of "Nicholas Long", so it matches at L3 (0.95) through the
   stored alias. An alias whose normalized form happens to
   equal the canonical key is found at L2 first — the layers
   are tried in order and the first hit wins.

2. The merge — `merge_entities`. Folds a duplicate entity into
   a canonical one: every foreign key the duplicate owns is
   re-pointed (knowledge assets, opportunities, skills,
   certifications, family records, legacy assets, state
   vectors, entity relationships — self-loops created by the
   re-point are deleted, not kept); the duplicate's name and
   aliases move into the canonical's aliases (deduped by
   normalized key); the duplicate's graph node is deleted
   (its edges cascade) and the canonical node plus every moved
   record's edges are re-synced; an audit entry is written.
   The caller commits.

Identity rules the engine enforces, stated plainly:

- Normalization: casefold; strip accents; replace "&" with a
  space; delete punctuation; tokenize on whitespace; drop
  legal-form tokens (inc, llc, corp, corporation, co, company,
  ltd, limited, lp, llp, nonprofit, non-profit, 501c3, 501c)
  and a leading "the"; join the remaining tokens with NO
  separator. Tokens are NOT sorted (order is part of the
  identity: "Long Nicholas" is not "Nicholas Long"), and
  "foundation" is NOT stripped (it distinguishes real orgs —
  e.g. Roundhouse Foundation). "N. Long" normalizes to
  "nlong", which deliberately does NOT match "nicholaslong"
  at L2 — an initial is not a name, and only an alias (or a
  human) may assert that identity.
- Matching is scoped to one entity type and to the entities
  the caller can see (their own plus shared). A funder is
  resolved as a business entity; the operator's NicNacs
  nonprofit entity is a different type and never collides.
- JSON alias lists are reassigned, never mutated in place —
  SQLAlchemy does not track in-place mutation of a plain JSON
  column.

Metrics (see `compute_metrics` and `resolution_measures`):
the Architect's measures are identity-quality measures, not
graph-size measures. Entity Resolution Rate = Previously
Distinct Duplicates / Resolved Canonical Entities. Duplicate
Compression Ratio = Before Resolution / After Resolution.
Duplicate Reduction % = (Before - After) / Before — the
Canon example 5 -> 1 is 80%, not 90%; the 90% figure was
mathematically wrong and is not used. The target is One
Entity = One Node: after resolution, zero duplicate groups
remain. Total node count may correctly RISE after resolution
(a funder that was free text becomes a real node); what must
fall is the duplicate count, and what must rise is the
relationship density and the findings on discovery replay.
"""

from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph import build_graph_service
from app.models.entity import Entity, EntityRelationship
from app.models.entity_resolution import (
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    EntityResolutionReview,
)
from app.models.graph import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationshipType,
)
from app.models.opportunity import Opportunity
from app.models.state_vector import StateVector
from app.models.user import User
from app.models.vault import (
    Certification,
    FamilyRecord,
    KnowledgeAsset,
    LegacyAsset,
    Skill,
)
from app.services.vault_service import log_event

# --- Audit event types (free strings, like the Sprint 2/4 ones). ---
EVENT_ENTITY_CREATED = "entity_created"
EVENT_ENTITY_RESOLVED = "entity_resolved"
EVENT_ENTITY_MERGED = "entity_merged"
EVENT_RESOLUTION_REVIEW_QUEUED = "resolution_review_queued"
EVENT_RESOLUTION_REVIEW_APPROVED = "resolution_review_approved"
EVENT_RESOLUTION_REVIEW_REJECTED = "resolution_review_rejected"

# --- Layer confidences (Canon §10, Phase C.1 plan). ---
CONFIDENCE_L1 = 1.00
CONFIDENCE_L2 = 0.98
CONFIDENCE_L3 = 0.95
CONFIDENCE_L4_DOMAIN = 0.85
CONFIDENCE_L4_STATE_TOKEN = 0.75

# Tokens that carry no identity: legal forms and designators.
# "foundation" is deliberately NOT in this set — it
# distinguishes real organizations (e.g. Roundhouse Foundation).
_LEGAL_TOKENS = frozenset(
    {
        "inc",
        "llc",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "lp",
        "llp",
        "nonprofit",
        "non-profit",
        "501c3",
        "501c",
    }
)
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)
_URL_LINE_RE = re.compile(r"^URL: (\S+)\s*$", re.M)
_STATE_LINE_RE = re.compile(
    r"^Place of performance \(state\): (\S+)\s*$", re.M
)


def tokens_of(raw: str) -> list[str]:
    """The normalized tokens of a name, before the no-separator
    join. Shared with the L4 shared-token signal."""
    if not raw:
        return []
    text = unicodedata.normalize("NFKD", raw.casefold())
    text = "".join(
        char for char in text if not unicodedata.combining(char)
    )
    text = text.replace("&", " ")
    # Punctuation is DELETED, not replaced with a space: that is
    # what lets "501(c)(3)" become the single token "501c3" (and
    # be dropped) and "non-profit" become "nonprofit".
    text = text.translate(_PUNCT_TABLE)
    tokens = text.split()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    return [t for t in tokens if t not in _LEGAL_TOKENS]


def normalize_name(raw: str) -> str:
    """The identity key of a name.

    "NicNacs", "NICNACS", "Nic Nacs", "NicNacs Inc", and
    "NicNacs 501(c)(3)" all normalize to "nicnacs". "N. Long"
    normalizes to "nlong" — deliberately not "nicholaslong".
    An empty or all-legal-form name falls back to the raw
    alphanumerics (or "unknown") so the key is never empty.
    """
    key = "".join(tokens_of(raw or ""))
    if key:
        return key
    fallback = re.sub(r"\W+", "", (raw or "").casefold())
    return fallback or "unknown"


def display_name(raw: str) -> str:
    """The canonical display form: the first-seen spelling,
    whitespace-collapsed, casing as given."""
    return " ".join((raw or "").split())


@dataclass
class ResolutionResult:
    """What the resolver did for one incoming name."""

    entity: Entity
    confidence: float
    layer: str  # "L1" | "L2" | "L3" | "L4" | "NEW"
    review_id: int | None = None


# --- Candidate scoping ---


async def _candidate_entities(
    db: AsyncSession, entity_type: str, owner_id: int | None
) -> list[Entity]:
    """Entities of one type the caller can see: their own plus
    shared (unowned) ones. When owner_id is None (an import
    resolving a shared org), only shared entities are candidates
    — an import never attaches to a user's private entity."""
    stmt = select(Entity).where(Entity.type == entity_type)
    if owner_id is None:
        stmt = stmt.where(Entity.owner_id.is_(None))
    else:
        stmt = stmt.where(
            or_(
                Entity.owner_id == owner_id,
                Entity.owner_id.is_(None),
            )
        )
    stmt = stmt.order_by(Entity.id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _alias_keys(entity: Entity) -> set[str]:
    return {normalize_name(a) for a in (entity.aliases or [])}


async def _record_alias(db: AsyncSession, entity: Entity, spelling: str):
    """Record an incoming spelling as an alias of an entity
    when it differs from the canonical name and is not already
    listed. The list is reassigned, never mutated in place
    (SQLAlchemy does not track in-place JSON mutation)."""
    spelling = (spelling or "").strip()
    if not spelling or spelling == entity.canonical_name:
        return False
    aliases = list(entity.aliases or [])
    if spelling in aliases:
        return False
    entity.aliases = aliases + [spelling]
    await db.flush()
    return True


# --- L4 context candidates ---


def _url_host(asset: KnowledgeAsset) -> str | None:
    """The host of the URL the import recorded in the asset's
    description ("URL: <url>"), or None. The import writes the
    line in a fixed format; anything else is not a URL."""
    match = _URL_LINE_RE.search(asset.description or "")
    if not match or match.group(1) == "unverified":
        return None
    try:
        return (urlparse(match.group(1)).hostname or "").lower() or None
    except ValueError:
        return None


def _asset_state(asset: KnowledgeAsset) -> str | None:
    """The place-of-performance state the contract import
    recorded ("Place of performance (state): <state>")."""
    match = _STATE_LINE_RE.search(asset.description or "")
    return match.group(1) if match else None


async def _org_entities_for_asset_edges(
    db: AsyncSession, *, content_type: str, relationship_type: str
) -> dict[int, Entity]:
    """Map asset id -> the org entity its FUNDED_BY / ISSUED_BY
    edge points at. Only assets that already have such an edge
    appear (an asset whose funder was free text has none)."""
    assets = (
        await db.execute(
            select(KnowledgeAsset).where(
                KnowledgeAsset.content_type == content_type
            )
        )
    ).scalars().all()
    mapping: dict[int, Entity] = {}
    for asset in assets:
        node = (
            await db.execute(
                select(GraphNode).where(
                    GraphNode.node_type == GraphNodeType.KNOWLEDGE_ASSET.value,
                    GraphNode.source_id == asset.id,
                )
            )
        ).scalar_one_or_none()
        if node is None:
            continue
        edge = (
            await db.execute(
                select(GraphEdge).where(
                    GraphEdge.from_node_id == node.id,
                    GraphEdge.relationship_type == relationship_type,
                )
            )
        ).scalar_one_or_none()
        if edge is None:
            continue
        org_node = await db.get(GraphNode, edge.to_node_id)
        if org_node is None or org_node.node_type != GraphNodeType.ENTITY.value:
            continue
        org = await db.get(Entity, org_node.source_id)
        if org is not None:
            mapping[asset.id] = org
    return mapping


async def _find_context_candidates(
    db: AsyncSession,
    *,
    name: str,
    entity_type: str,
    context: dict,
    owner_id: int | None,
) -> list[tuple[Entity, float, dict]]:
    """L4 candidates: (entity, confidence, signal). Only what
    the feeds actually contain:
    - domain equality: a grant URL host shared with an asset
      already linked to an org entity (0.85);
    - same state plus a shared token (length >= 4, not a legal
      token) with a contract agency's entity (0.75).
    The caller tries L1-L3 first; this runs only on a miss."""
    found: dict[int, tuple[Entity, float, dict]] = {}

    domain = (context.get("domain") or "").strip().lower() or None
    if domain:
        by_asset = await _org_entities_for_asset_edges(
            db,
            content_type="grant",
            relationship_type=GraphRelationshipType.FUNDED_BY.value,
        )
        assets = (
            await db.execute(
                select(KnowledgeAsset).where(
                    KnowledgeAsset.content_type == "grant"
                )
            )
        ).scalars().all()
        for asset in assets:
            if asset.id not in by_asset:
                continue
            if _url_host(asset) != domain:
                continue
            org = by_asset[asset.id]
            if org.type != entity_type:
                continue
            if owner_id is None and org.owner_id is not None:
                continue
            if owner_id is not None and org.owner_id not in (None, owner_id):
                continue
            found[org.id] = (
                org,
                CONFIDENCE_L4_DOMAIN,
                {"signal": "domain", "domain": domain},
            )

    state = (context.get("state") or "").strip() or None
    if state:
        by_asset = await _org_entities_for_asset_edges(
            db,
            content_type="sam-gov-contract",
            relationship_type=GraphRelationshipType.ISSUED_BY.value,
        )
        assets = (
            await db.execute(
                select(KnowledgeAsset).where(
                    KnowledgeAsset.content_type == "sam-gov-contract"
                )
            )
        ).scalars().all()
        input_tokens = {
            t for t in tokens_of(name) if len(t) >= 4
        }
        for asset in assets:
            if asset.id not in by_asset:
                continue
            if _asset_state(asset) != state:
                continue
            org = by_asset[asset.id]
            if org.type != entity_type:
                continue
            if owner_id is None and org.owner_id is not None:
                continue
            if owner_id is not None and org.owner_id not in (None, owner_id):
                continue
            org_tokens = {
                t
                for t in tokens_of(org.canonical_name)
                if len(t) >= 4
            } | {
                t
                for alias in (org.aliases or [])
                for t in tokens_of(alias)
                if len(t) >= 4
            }
            shared = sorted(input_tokens & org_tokens)
            if not shared:
                continue
            prev = found.get(org.id)
            if prev is None or CONFIDENCE_L4_STATE_TOKEN > prev[1]:
                found[org.id] = (
                    org,
                    CONFIDENCE_L4_STATE_TOKEN,
                    {"signal": "state_token", "state": state, "shared": shared},
                )
    return list(found.values())


async def _existing_review(
    db: AsyncSession, *, normalized_name: str, matched_entity_id: int
) -> EntityResolutionReview | None:
    """A pending or rejected review for the same (proposed,
    matched) pair — a rejected pair is suppressed from
    re-queuing, and a pending one is not duplicated."""
    return (
        await db.execute(
            select(EntityResolutionReview).where(
                EntityResolutionReview.normalized_name == normalized_name,
                EntityResolutionReview.matched_entity_id == matched_entity_id,
                EntityResolutionReview.status.in_(
                    [REVIEW_STATUS_PENDING, REVIEW_STATUS_REJECTED]
                ),
            )
        )
    ).scalar_one_or_none()


# --- The resolver ---


async def resolve_or_create(
    db: AsyncSession,
    *,
    name: str,
    entity_type: str,
    context: dict | None = None,
    owner_id: int | None = None,
    user: User,
) -> ResolutionResult:
    """Resolve one incoming name to an entity, creating one when
    no layer matches. L1-L3 auto-attach (recording the incoming
    spelling as an alias when it differs). L4 creates a NEW
    entity and queues a review proposing the merge — it never
    auto-attaches. The caller commits."""
    raw = (name or "").strip()
    if not raw:
        raise ValueError("resolve_or_create requires a non-empty name")
    key = normalize_name(raw)
    ctx = dict(context or {})
    candidates = await _candidate_entities(db, entity_type, owner_id)

    # L1 — exact match on canonical_name or name.
    for entity in candidates:
        if entity.canonical_name == raw or entity.name == raw:
            await _record_alias(db, entity, raw)
            log_event(
                db,
                event_type=EVENT_ENTITY_RESOLVED,
                entity_id=entity.id,
                record_type="Entity",
                record_id=entity.id,
                actor=user.email,
                details={"layer": "L1", "name": raw},
            )
            return ResolutionResult(
                entity=entity, confidence=CONFIDENCE_L1, layer="L1"
            )
    # L2 — normalized match against canonical keys.
    for entity in candidates:
        if normalize_name(entity.canonical_name) == key:
            await _record_alias(db, entity, raw)
            log_event(
                db,
                event_type=EVENT_ENTITY_RESOLVED,
                entity_id=entity.id,
                record_type="Entity",
                record_id=entity.id,
                actor=user.email,
                details={"layer": "L2", "name": raw},
            )
            return ResolutionResult(
                entity=entity, confidence=CONFIDENCE_L2, layer="L2"
            )
    # L3 — normalized match against alias keys.
    for entity in candidates:
        if key in _alias_keys(entity):
            await _record_alias(db, entity, raw)
            log_event(
                db,
                event_type=EVENT_ENTITY_RESOLVED,
                entity_id=entity.id,
                record_type="Entity",
                record_id=entity.id,
                actor=user.email,
                details={"layer": "L3", "name": raw},
            )
            return ResolutionResult(
                entity=entity, confidence=CONFIDENCE_L3, layer="L3"
            )

    # L4 — context candidates. A new entity is created either
    # way; the candidate only ever becomes a queued review.
    l4 = await _find_context_candidates(
        db,
        name=raw,
        entity_type=entity_type,
        context=ctx,
        owner_id=owner_id,
    )
    display = display_name(raw)
    entity = Entity(
        type=entity_type,
        name=raw,
        canonical_name=display,
        aliases=[raw] if raw != display else [],
        confidence_score=CONFIDENCE_L1,
        owner_id=owner_id,
    )
    db.add(entity)
    await db.flush()
    # The new entity's graph node is created here (the API and
    # import paths create nodes for their own records; the
    # resolver owns its org entities' nodes).
    service = build_graph_service(db)
    await service.sync_entity(entity)
    log_event(
        db,
        event_type=EVENT_ENTITY_CREATED,
        entity_id=entity.id,
        record_type="Entity",
        record_id=entity.id,
        actor=user.email,
        details={"layer": "NEW", "name": raw},
    )
    if not l4:
        return ResolutionResult(
            entity=entity, confidence=CONFIDENCE_L1, layer="NEW"
        )
    best_entity, best_conf, signal = max(l4, key=lambda item: item[1])
    entity.confidence_score = best_conf
    # Suppression: a rejected (proposed, matched) pair is never
    # re-queued, and a pending review for the same pair is not
    # duplicated.
    existing = await _existing_review(
        db, normalized_name=key, matched_entity_id=best_entity.id
    )
    if existing is not None:
        return ResolutionResult(
            entity=entity, confidence=best_conf, layer="L4", review_id=None
        )
    review = EntityResolutionReview(
        proposed_name=raw,
        normalized_name=key,
        entity_type=entity_type,
        matched_entity_id=best_entity.id,
        layer="L4",
        confidence=best_conf,
        context={**ctx, **signal},
    )
    db.add(review)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_RESOLUTION_REVIEW_QUEUED,
        entity_id=entity.id,
        record_type="EntityResolutionReview",
        record_id=review.id,
        actor=user.email,
        details={
            "proposed": raw,
            "matched_entity_id": best_entity.id,
            "confidence": best_conf,
        },
    )
    return ResolutionResult(
        entity=entity, confidence=best_conf, layer="L4", review_id=review.id
    )


# --- The merge ---

# Every table whose rows an entity owns (AuditLog is history
# and is deliberately NOT re-pointed — it records what happened,
# not what is true now).
_FK_TABLES: list[tuple[type, str]] = [
    (KnowledgeAsset, "entity_id"),
    (Opportunity, "entity_id"),
    (Skill, "entity_id"),
    (Certification, "entity_id"),
    (FamilyRecord, "entity_id"),
    (LegacyAsset, "entity_id"),
    (StateVector, "entity_id"),
]


async def merge_entities(
    db: AsyncSession,
    *,
    canonical_id: int,
    duplicate_id: int,
    user: User,
) -> dict:
    """Fold a duplicate entity into a canonical one. Re-points
    every foreign key the duplicate owns, moves the duplicate's
    name and aliases into the canonical's aliases (deduped by
    normalized key), deletes the duplicate's graph node (its
    edges cascade) and the duplicate row, re-syncs the canonical
    node and every moved record's edges, and writes an audit
    entry. The caller commits."""
    if canonical_id == duplicate_id:
        raise ValueError("merge_entities: canonical and duplicate are the same")
    canonical = await db.get(Entity, canonical_id)
    duplicate = await db.get(Entity, duplicate_id)
    if canonical is None or duplicate is None:
        raise ValueError("merge_entities: canonical or duplicate entity not found")

    # Collect the moved records first (their ids are needed for
    # the re-sync after the bulk re-point).
    moved: dict[str, list[int]] = {}
    for model, column in _FK_TABLES:
        rows = (
            await db.execute(
                select(model.id).where(getattr(model, column) == duplicate_id)
            )
        ).scalars().all()
        moved[model.__name__] = list(rows)

    # Bulk re-point every owned foreign key.
    for model, column in _FK_TABLES:
        await db.execute(
            update(model)
            .where(getattr(model, column) == duplicate_id)
            .values(**{column: canonical_id})
        )
    # Entity relationships: re-point both ends; a row that
    # becomes a self-loop (duplicate <-> canonical) is deleted.
    rels = (
        await db.execute(
            select(EntityRelationship).where(
                or_(
                    EntityRelationship.from_entity_id == duplicate_id,
                    EntityRelationship.to_entity_id == duplicate_id,
                )
            )
        )
    ).scalars().all()
    relationships_deleted = 0
    for rel in rels:
        new_from = (
            canonical_id
            if rel.from_entity_id == duplicate_id
            else rel.from_entity_id
        )
        new_to = (
            canonical_id
            if rel.to_entity_id == duplicate_id
            else rel.to_entity_id
        )
        if new_from == new_to:
            await db.delete(rel)
            relationships_deleted += 1
        else:
            rel.from_entity_id = new_from
            rel.to_entity_id = new_to
    await db.flush()

    # Aliases: the duplicate's name and aliases move into the
    # canonical's aliases, deduped by normalized key (the list
    # is reassigned — never mutated in place).
    seen_keys = {normalize_name(canonical.canonical_name)} | {
        normalize_name(a) for a in (canonical.aliases or [])
    }
    aliases_before = len(canonical.aliases or [])
    aliases = list(canonical.aliases or [])
    for spelling in (
        [duplicate.name, duplicate.canonical_name]
        + list(duplicate.aliases or [])
    ):
        spelling = (spelling or "").strip()
        if not spelling:
            continue
        if normalize_name(spelling) in seen_keys:
            continue
        if spelling == canonical.canonical_name:
            continue
        aliases.append(spelling)
        seen_keys.add(normalize_name(spelling))
    canonical.aliases = aliases

    # Graph: capture the duplicate node's edges BEFORE deleting
    # it, so they can be re-pointed at the canonical node. The
    # edges that matter here are the ones that do NOT belong to
    # records the duplicate owns: a FUNDED_BY / ISSUED_BY edge
    # lives on the asset node (owned by the operator entity) and
    # points at the org node, so the moved-records re-sync below
    # would never recreate it — without this step the merge
    # would silently drop the org's funding links.
    graph = build_graph_service(db)
    dup_node = await graph.repo.get_node_by_source(
        GraphNodeType.ENTITY.value, duplicate_id
    )
    captured_incoming: list[GraphEdge] = []
    captured_outgoing: list[GraphEdge] = []
    if dup_node is not None:
        captured_incoming = list(
            (
                await db.execute(
                    select(GraphEdge).where(
                        GraphEdge.to_node_id == dup_node.id
                    )
                )
            ).scalars().all()
        )
        captured_outgoing = list(
            (
                await db.execute(
                    select(GraphEdge).where(
                        GraphEdge.from_node_id == dup_node.id
                    )
                )
            ).scalars().all()
        )
    # Delete the duplicate's node (its edges cascade), then
    # re-sync the canonical node and every moved record's edges
    # from the canonical node.
    await graph.delete_node_for_source(
        GraphNodeType.ENTITY.value, duplicate_id
    )
    await db.flush()
    # NOTE: no expire_all() here. The captured edges above are
    # the re-point loop's only copy of rows the node delete just
    # removed — expiring them would force a lazy load of rows
    # that no longer exist. The in-memory values are exactly
    # what the re-point needs.
    canonical = await db.get(Entity, canonical_id)
    duplicate = await db.get(Entity, duplicate_id)
    canon_node = await graph.sync_entity(canonical)
    _syncers = {
        "KnowledgeAsset": graph.sync_knowledge_asset,
        "Skill": graph.sync_skill,
        "Certification": graph.sync_certification,
        "FamilyRecord": graph.sync_family_record,
        "LegacyAsset": graph.sync_legacy_asset,
    }
    _models = {
        "KnowledgeAsset": KnowledgeAsset,
        "Skill": Skill,
        "Certification": Certification,
        "FamilyRecord": FamilyRecord,
        "LegacyAsset": LegacyAsset,
    }
    for model_name, ids in moved.items():
        syncer = _syncers.get(model_name)
        if syncer is None:
            continue
        for record_id in ids:
            record = await db.get(_models[model_name], record_id)
            if record is not None:
                await syncer(record)
    # Re-point the captured edges at the canonical node. Edges
    # the moved-records re-sync already recreated are no-ops
    # (ensure_edge is get-or-create); edges that would become
    # self-loops (duplicate <-> canonical) are skipped.
    for edge in captured_incoming:
        if edge.from_node_id == canon_node.id:
            continue
        from_node = await db.get(GraphNode, edge.from_node_id)
        if from_node is None:
            continue
        await graph.ensure_edge(
            from_node,
            canon_node,
            edge.relationship_type,
            confidence=edge.confidence,
            source=edge.source,
        )
    for edge in captured_outgoing:
        if edge.to_node_id == canon_node.id:
            continue
        to_node = await db.get(GraphNode, edge.to_node_id)
        if to_node is None:
            continue
        await graph.ensure_edge(
            canon_node,
            to_node,
            edge.relationship_type,
            confidence=edge.confidence,
            source=edge.source,
        )
    # Delete the duplicate row with a Core DELETE, not the ORM
    # delete: Entity carries cascade="all, delete-orphan"
    # relationships, and an ORM delete would lazy-load those
    # collections (sync IO, illegal inside async SQLAlchemy once
    # the instance's state is expired). Every foreign key the
    # duplicate owned was already re-pointed above, and its
    # entity-relationship rows were re-pointed or deleted, so no
    # ORM cascade has anything left to do. AuditLog is history
    # and is deliberately not re-pointed.
    await db.execute(delete(Entity).where(Entity.id == duplicate_id))
    await db.flush()
    log_event(
        db,
        event_type=EVENT_ENTITY_MERGED,
        entity_id=canonical_id,
        record_type="Entity",
        record_id=canonical_id,
        actor=user.email,
        details={
            "duplicate_id": duplicate_id,
            "duplicate_name": duplicate.name,
            "moved": {k: len(v) for k, v in moved.items() if v},
            "relationships_deleted": relationships_deleted,
        },
    )
    return {
        "canonical_id": canonical_id,
        "duplicate_id": duplicate_id,
        "moved": {k: len(v) for k, v in moved.items() if v},
        "relationships_deleted": relationships_deleted,
        "aliases_added": len(aliases) - aliases_before,
    }


# --- Asset -> org edges (used by the imports) ---


async def link_asset_to_org(
    db: AsyncSession,
    *,
    asset: KnowledgeAsset,
    org: Entity,
    relationship_type: str,
    confidence: float,
) -> bool:
    """Create the FUNDED_BY / ISSUED_BY edge from an asset's
    node to an org entity's node (source "entity_resolution").
    A non-OWNS edge also closes the D4 "no relationships" gap
    for that asset. Returns True when the edge was newly
    created (False when it already existed)."""
    graph = build_graph_service(db)
    asset_node = await graph.repo.get_node_by_source(
        GraphNodeType.KNOWLEDGE_ASSET.value, asset.id
    )
    if asset_node is None:
        asset_node = await graph.sync_knowledge_asset(asset)
    org_node = await graph.repo.get_node_by_source(
        GraphNodeType.ENTITY.value, org.id
    )
    if org_node is None:
        org_node = await graph.sync_entity(org)
    existing = await graph.repo.get_edge_by_triple(
        asset_node.id, org_node.id, relationship_type
    )
    await graph.ensure_edge(
        asset_node,
        org_node,
        relationship_type,
        confidence=confidence,
        source="entity_resolution",
    )
    return existing is None


# --- Metrics ---


async def compute_metrics(
    db: AsyncSession, run_counts: dict | None = None
) -> dict:
    """Live identity-quality measures of the database, plus any
    per-run measures the caller supplies (resolution counts over
    one run). Node totals and edge totals are raw graph size —
    they are reported, never judged: resolution may correctly
    ADD nodes (a funder that was free text becomes a real node).
    What is judged is the duplicate count (must fall to zero)
    and the relationship density and findings on replay (must
    rise)."""
    entities = (await db.execute(select(Entity))).scalars().all()
    with_aliases = sum(1 for e in entities if (e.aliases or []))
    # Duplicate groups: same type + same normalized canonical
    # key, more than one entity.
    groups: dict[tuple[str, str], list[Entity]] = {}
    for entity in entities:
        groups.setdefault(
            (entity.type, normalize_name(entity.canonical_name)), []
        ).append(entity)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    dup_entities = sum(len(v) for v in dup_groups.values())
    nodes_total = int(
        (await db.execute(select(func.count()).select_from(GraphNode))).scalar_one()
    )
    edges_total = int(
        (await db.execute(select(func.count()).select_from(GraphEdge))).scalar_one()
    )
    edges_by_type_rows = (
        await db.execute(
            select(GraphEdge.relationship_type, func.count())
            .group_by(GraphEdge.relationship_type)
            .order_by(GraphEdge.relationship_type)
        )
    ).all()
    edges_by_type = {row[0]: int(row[1]) for row in edges_by_type_rows}
    beyond_owns = edges_total - edges_by_type.get(
        GraphRelationshipType.OWNS.value, 0
    )
    metrics: dict = {
        "entities_total": len(entities),
        "entities_with_aliases": with_aliases,
        "canonical_coverage": (with_aliases / len(entities) if entities else 0.0),
        "duplicate_groups": len(dup_groups),
        "duplicate_entities": dup_entities,
        "duplicate_group_sizes": sorted(
            (len(v) for v in dup_groups.values()), reverse=True
        ),
        "nodes_total": nodes_total,
        "edges_total": edges_total,
        "edges_by_type": edges_by_type,
        "relationships_beyond_owns": beyond_owns,
        "relationship_density": (
            beyond_owns / nodes_total if nodes_total else 0.0
        ),
    }
    if run_counts:
        metrics["run"] = dict(run_counts)
    return metrics


def resolution_measures(before: dict, after: dict) -> dict:
    """The Architect's before/after measures, computed from two
    compute_metrics snapshots. Reported raw alongside the
    ratios, so the arithmetic is checkable:
    - Entity Resolution Rate = Previously Distinct Duplicates /
      Resolved Canonical Entities (the duplicate entities that
      existed before, over the canonical entities they resolved
      into).
    - Duplicate Compression Ratio = Before Resolution / After
      Resolution (total entity counts).
    - Duplicate Reduction % = (Before - After) / Before — the
      Canon example 5 -> 1 is 80%, not 90%.
    The target is One Entity = One Node: duplicate_groups
    after the run must be 0."""
    dup_before = before.get("duplicate_entities", 0)
    merged = before.get("entities_total", 0) - after.get("entities_total", 0)
    canonical_formed = dup_before - merged if dup_before else 0
    entities_before = before.get("entities_total", 0)
    entities_after = after.get("entities_total", 0)
    return {
        "previously_distinct_duplicates": dup_before,
        "resolved_canonical_entities": canonical_formed,
        "entity_resolution_rate": (
            dup_before / canonical_formed if canonical_formed else 0.0
        ),
        "duplicate_compression_ratio": (
            entities_before / entities_after if entities_after else 0.0
        ),
        "duplicate_reduction_pct": (
            (entities_before - entities_after) / entities_before * 100
            if entities_before
            else 0.0
        ),
        "duplicate_groups_before": before.get("duplicate_groups", 0),
        "duplicate_groups_after": after.get("duplicate_groups", 0),
        "relationship_density_before": before.get("relationship_density", 0.0),
        "relationship_density_after": after.get("relationship_density", 0.0),
        "relationships_beyond_owns_before": before.get(
            "relationships_beyond_owns", 0
        ),
        "relationships_beyond_owns_after": after.get(
            "relationships_beyond_owns", 0
        ),
    }


# --- Review queue ---


async def list_pending_reviews(
    db: AsyncSession, *, status: str = REVIEW_STATUS_PENDING
) -> list[EntityResolutionReview]:
    """Review rows in one status, newest first."""
    result = await db.execute(
        select(EntityResolutionReview)
        .where(EntityResolutionReview.status == status)
        .order_by(
            EntityResolutionReview.created_at.desc(),
            EntityResolutionReview.id.desc(),
        )
    )
    return list(result.scalars().all())


async def _find_by_normalized_key(
    db: AsyncSession, normalized_name: str, entity_type: str
) -> Entity | None:
    """The entity whose normalized canonical key equals the
    given key (earliest-created wins if several — pre-resolution
    data can hold duplicates)."""
    entities = (
        await db.execute(
            select(Entity)
            .where(Entity.type == entity_type)
            .order_by(Entity.id)
        )
    ).scalars().all()
    for entity in entities:
        if normalize_name(entity.canonical_name) == normalized_name:
            return entity
    return None


async def approve_review(
    db: AsyncSession, *, review_id: int, user: User
) -> dict:
    """Approve a pending review: merge the proposed entity into
    the matched (canonical) one. The proposed entity is found by
    the review's normalized_name. The caller commits."""
    review = await db.get(EntityResolutionReview, review_id)
    if review is None:
        raise ValueError(f"review {review_id} not found")
    if review.status != REVIEW_STATUS_PENDING:
        raise ValueError(f"review {review_id} is not pending")
    if review.matched_entity_id is None:
        raise ValueError(f"review {review_id} names no candidate entity")
    matched = await db.get(Entity, review.matched_entity_id)
    if matched is None:
        raise ValueError("review candidate entity no longer exists")
    proposed = await _find_by_normalized_key(
        db, review.normalized_name, review.entity_type
    )
    if proposed is None:
        raise ValueError("review proposed entity no longer exists")
    if proposed.id == matched.id:
        # Already the same entity (a merge landed another way).
        review.status = REVIEW_STATUS_APPROVED
        review.resolved_at = datetime.now(timezone.utc)
        await db.flush()
        return {
            "canonical_id": matched.id,
            "duplicate_id": proposed.id,
            "already_merged": True,
        }
    result = await merge_entities(
        db, canonical_id=matched.id, duplicate_id=proposed.id, user=user
    )
    review.status = REVIEW_STATUS_APPROVED
    review.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_RESOLUTION_REVIEW_APPROVED,
        entity_id=matched.id,
        record_type="EntityResolutionReview",
        record_id=review.id,
        actor=user.email,
        details={"duplicate_id": proposed.id},
    )
    result["already_merged"] = False
    return result


async def reject_review(
    db: AsyncSession, *, review_id: int, user: User
) -> EntityResolutionReview:
    """Reject a pending review. The (proposed, matched) pair is
    suppressed from future re-queuing — a rejection is a human
    judgment that the two are different entities. The caller
    commits."""
    review = await db.get(EntityResolutionReview, review_id)
    if review is None:
        raise ValueError(f"review {review_id} not found")
    if review.status != REVIEW_STATUS_PENDING:
        raise ValueError(f"review {review_id} is not pending")
    review.status = REVIEW_STATUS_REJECTED
    review.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_RESOLUTION_REVIEW_REJECTED,
        entity_id=review.matched_entity_id,
        record_type="EntityResolutionReview",
        record_id=review.id,
        actor=user.email,
        details={"proposed": review.proposed_name},
    )
    return review


# --- The resolution pass (CLI) ---

_FUNDER_LINE_RE = re.compile(r"^Funder: (.+)$", re.M)
_AGENCY_LINE_RE = re.compile(r"^Agency: (.+)$", re.M)


async def resolution_pass(
    db: AsyncSession, *, user: User, dry_run: bool = False
) -> dict:
    """One pass over the whole database: find duplicate
    entities, merge the unambiguous ones, queue reviews for the
    ambiguous ones, and report the measures.

    - Canonical-key groups (same type + same normalized
      canonical key, >1 entity) are merged into the
      earliest-created member. This is the unambiguous case:
      the keys are identical, so the entities are the same
      real-world thing spelled the same way after
      normalization.
    - Alias-key overlaps (an alias key claimed by two distinct
      entities) and domain-shared org groups (one URL host
      funding two distinct org entities) are QUEUED as reviews,
      never auto-merged — the shared key may be a coincidence
      ("N. Long") and only a human may assert the identity.
    - Assets are scanned by normalized (funder/agency + title)
      for near-duplicates the slug key missed. The grants
      import's slug key already normalizes case and
      punctuation, so additional collapse is expected to be ~0
      — the real number is reported, never assumed. Assets are
      NOT merged by this pass (no asset merge exists); the
      groups are reported for the operator.

    In dry-run mode nothing is written. Otherwise the caller
    commits."""
    before = await compute_metrics(db)
    entities = (
        await db.execute(select(Entity).order_by(Entity.id))
    ).scalars().all()

    # Canonical-key groups.
    key_groups: dict[tuple[str, str], list[Entity]] = {}
    for entity in entities:
        key_groups.setdefault(
            (entity.type, normalize_name(entity.canonical_name)), []
        ).append(entity)
    dup_key_groups = {
        k: v for k, v in key_groups.items() if len(v) > 1
    }

    # Alias-key overlaps: an alias key claimed by entities
    # other than the one that owns the alias.
    alias_claims: dict[tuple[str, str], set[int]] = {}
    by_id = {e.id: e for e in entities}
    for entity in entities:
        for alias in entity.aliases or []:
            alias_claims.setdefault(
                (entity.type, normalize_name(alias)), set()
            ).add(entity.id)
    alias_overlaps: list[dict] = []
    for (etype, akey), ids in alias_claims.items():
        if len(ids) < 2:
            continue
        members = sorted(ids)
        # The alias key also matches a canonical key of one of
        # the members — that is the L2 case, already handled by
        # the canonical-key groups; only a purely-alias overlap
        # (no member's canonical key equals the alias key) is
        # ambiguous enough to queue.
        if any(
            normalize_name(by_id[i].canonical_name) == akey
            for i in members
        ):
            continue
        alias_overlaps.append(
            {
                "entity_type": etype,
                "alias_key": akey,
                "entity_ids": members,
            }
        )

    # Domain-shared org groups: one grant URL host funding two
    # distinct org entities (via FUNDED_BY edges).
    domain_orgs: dict[str, set[int]] = {}
    grant_assets = (
        await db.execute(
            select(KnowledgeAsset).where(
                KnowledgeAsset.content_type == "grant"
            )
        )
    ).scalars().all()
    by_asset = await _org_entities_for_asset_edges(
        db,
        content_type="grant",
        relationship_type=GraphRelationshipType.FUNDED_BY.value,
    )
    for asset in grant_assets:
        org = by_asset.get(asset.id)
        host = _url_host(asset)
        if org is None or not host:
            continue
        domain_orgs.setdefault(host, set()).add(org.id)
    domain_groups = [
        {"domain": host, "entity_ids": sorted(ids)}
        for host, ids in domain_orgs.items()
        if len(ids) > 1
    ]

    # Asset near-duplicates by normalized (funder/agency +
    # title). Reported only — never merged.
    asset_groups: dict[tuple[str, str], list[int]] = {}
    all_assets = (
        await db.execute(
            select(KnowledgeAsset).where(
                KnowledgeAsset.content_type.in_(
                    ["grant", "sam-gov-contract"]
                )
            )
        )
    ).scalars().all()
    for asset in all_assets:
        funder = ""
        match = _FUNDER_LINE_RE.search(asset.description or "")
        if match:
            funder = match.group(1).strip()
        else:
            match = _AGENCY_LINE_RE.search(asset.description or "")
            if match:
                funder = match.group(1).strip()
        asset_groups.setdefault(
            (normalize_name(funder), normalize_name(asset.title)), []
        ).append(asset.id)
    asset_dup_groups = [
        {"key": list(k), "asset_ids": v}
        for k, v in asset_groups.items()
        if len(v) > 1
    ]

    merges: list[dict] = []
    reviews_queued: list[dict] = []
    if not dry_run:
        # Merges first (canonical-key groups are unambiguous).
        for (etype, key), members in sorted(dup_key_groups.items()):
            canonical = members[0]
            for duplicate in members[1:]:
                result = await merge_entities(
                    db,
                    canonical_id=canonical.id,
                    duplicate_id=duplicate.id,
                    user=user,
                )
                merges.append(
                    {
                        "canonical_id": canonical.id,
                        "canonical_name": canonical.canonical_name,
                        "duplicate_id": duplicate.id,
                        "duplicate_name": duplicate.name,
                        "key": key,
                        "moved": result["moved"],
                    }
                )
        # Reviews for the ambiguous cases.
        async def _queue_review(
            *, proposed: Entity, matched: Entity, signal: dict
        ) -> None:
            if proposed.id == matched.id:
                return
            existing = await _existing_review(
                db,
                normalized_name=normalize_name(proposed.canonical_name),
                matched_entity_id=matched.id,
            )
            if existing is not None:
                return
            review = EntityResolutionReview(
                proposed_name=proposed.canonical_name,
                normalized_name=normalize_name(proposed.canonical_name),
                entity_type=proposed.type,
                matched_entity_id=matched.id,
                layer="L4",
                confidence=CONFIDENCE_L4_DOMAIN,
                context=signal,
            )
            db.add(review)
            await db.flush()
            reviews_queued.append(
                {
                    "review_id": review.id,
                    "proposed": proposed.canonical_name,
                    "matched_id": matched.id,
                    "matched_name": matched.canonical_name,
                    "signal": signal,
                }
            )

        for overlap in alias_overlaps:
            members = [by_id[i] for i in overlap["entity_ids"]]
            matched = members[0]
            for proposed in members[1:]:
                await _queue_review(
                    proposed=proposed,
                    matched=matched,
                    signal={
                        "signal": "alias_overlap",
                        "alias_key": overlap["alias_key"],
                    },
                )
        for group in domain_groups:
            members = [by_id[i] for i in group["entity_ids"]]
            matched = members[0]
            for proposed in members[1:]:
                await _queue_review(
                    proposed=proposed,
                    matched=matched,
                    signal={
                        "signal": "domain",
                        "domain": group["domain"],
                    },
                )
        await db.flush()

    after = await compute_metrics(db) if not dry_run else before
    return {
        "dry_run": dry_run,
        "before": before,
        "after": after,
        "measures": resolution_measures(before, after),
        "duplicate_key_groups": [
            {
                "entity_type": etype,
                "key": key,
                "entity_ids": [e.id for e in members],
                "names": [e.canonical_name for e in members],
            }
            for (etype, key), members in sorted(dup_key_groups.items())
        ],
        "alias_overlaps": alias_overlaps,
        "domain_groups": domain_groups,
        "asset_near_duplicate_groups": asset_dup_groups,
        "merges": merges,
        "reviews_queued": reviews_queued,
    }
