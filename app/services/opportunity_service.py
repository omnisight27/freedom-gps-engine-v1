"""Opportunity Engine — Engine v1 Sprint 4.

The first discovery capability. Mission: What is missing? What
should be connected? What creates value next? The system now has
Identity, State Vector, Memory, and Relationships; the next
logical capability is Discovery, and this is where Freedom GP$
first starts producing value that users can actually see.

Six detectors (all read the graph — they use GraphService and the
IGraphRepository primitives, never raw table filters where a graph
query expresses the finding):

- D1 skills_no_evidence — a skill with no outgoing HAS_EVIDENCE
  edge (uses the Sprint 3 graph query).
- D2 skills_evidence_no_utilization — a skill that HAS evidence
  but whose node has no edges beyond ownership (OWNS / HAS_SKILL
  in) and evidence (HAS_EVIDENCE out). HONEST DEFINITION: the
  system has no utilization data — Sprint 2 deliberately has no
  `utilized` field on Skill and there is no revenue linkage — so
  "no utilization" is operationalized as "verified capability
  that is not connected to any opportunity, asset, or other
  value-producing relationship". True utilization tracking
  (revenue linkage) is a later concern, not Sprint 4.
- D3 certifications_evidence_no_utilization — the same
  operational definition, applied to certifications.
- D4 knowledge_assets_no_relationships — a knowledge asset node
  whose only edge is the OWNS edge from its entity (zero edges
  counts too — it has even fewer relationships). NOTE: an asset
  that serves as a Skill / Certification's KnowledgeAsset
  representation has no graph edge to its holder (Sprint 3 does
  not record that link as an edge), so it can still be flagged
  here. The representation link is a source-table link, not a
  graph relationship.
- D5 legacy_no_preservation_status — a legacy asset with no
  preservation_status set (uses the Sprint 3 graph query).
- D6 disconnected_clusters — a connected component of the graph
  that contains no entity node: nodes connected to each other but
  not to the ledger. Found by BFS over the graph layer.

Detector -> opportunity type mapping:
D1 Skill, D2 Skill, D3 Knowledge, D4 Knowledge (Asset when the
asset's category is "Legacy Asset"), D5 Legacy, D6 Relationship.

Detection runs are idempotent: re-running the detectors never
creates a duplicate while an open (detected or accepted)
opportunity already exists for the same detector + source record
(D6 is keyed by its canonical cluster description). A gap that
closes dismisses its open opportunity automatically, with a note
appended to the description and an audit entry — except a cluster
opportunity whose description the user rewrote, which is left
alone (the description IS the cluster fingerprint; a rewritten
one can no longer be matched).

Confidence is the detector's certainty in the finding (0–1), not
a value estimate. The detector confidences are illustrative:
D1 0.9, D2 0.7, D3 0.7, D4 0.85, D5 0.9, D6 0.8.

Out of scope for Sprint 4 (do not add): AI Reasoning,
Forecasting, Simulation, Trust Engine v2, new scores. There is no
separate Recommendation model — the opportunity itself is the
output.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.service import GraphService
from app.models.entity import Entity
from app.models.graph import GraphNodeType, GraphRelationshipType
from app.models.opportunity import Opportunity, OpportunityStatus
from app.models.user import User
from app.models.vault import (
    Certification,
    EvidenceRecord,
    KnowledgeAsset,
    LegacyAsset,
    Skill,
)
from app.schemas.opportunity import OpportunityCreate, OpportunityUpdate
from app.services.entity_service import (
    assert_entity_access,
    assert_entity_owner,
    get_entity_or_404,
)
from app.services.vault_service import (
    accessible_entity_ids,
    get_record_or_404,
    log_event,
)

# --- Detector names (also the `detector` value stored on the
# opportunity) ---
DETECTOR_SKILLS_NO_EVIDENCE = "skills_no_evidence"
DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION = (
    "skills_evidence_no_utilization"
)
DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION = (
    "certifications_evidence_no_utilization"
)
DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS = "knowledge_assets_no_relationships"
DETECTOR_LEGACY_NO_PRESERVATION = "legacy_no_preservation_status"
DETECTOR_DISCONNECTED_CLUSTERS = "disconnected_clusters"

# Detector confidences (illustrative — the detector's certainty in
# the finding, not a value estimate).
CONFIDENCE: dict[str, float] = {
    DETECTOR_SKILLS_NO_EVIDENCE: 0.9,
    DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION: 0.7,
    DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION: 0.7,
    DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS: 0.85,
    DETECTOR_LEGACY_NO_PRESERVATION: 0.9,
    DETECTOR_DISCONNECTED_CLUSTERS: 0.8,
}

# Audit event types for opportunities (AT-407).
EVENT_OPPORTUNITY_CREATED = "opportunity_created"
EVENT_OPPORTUNITY_STATUS_CHANGED = "opportunity_status_changed"
EVENT_OPPORTUNITY_AUTO_DISMISSED = "opportunity_auto_dismissed"

# Statuses that count as "open" for idempotency: a new finding
# does not duplicate an open opportunity for the same gap.
OPEN_STATUSES = (
    OpportunityStatus.DETECTED.value,
    OpportunityStatus.ACCEPTED.value,
)

# Detectors the Opportunity Engine itself owns. run_detection
# only auto-dismisses opportunities created by these six — an
# opportunity created by an importer (a foreign detector value
# such as "contract_intel_sam_gov") is owned by its importer, and
# a detection run must never close it: the detectors cannot know
# whether the imported gap closed, and the import's idempotency
# contract is "dismissed is the user's decision". Found by
# Phase C (EO 007A): without this guard, the first learning pass
# over the populated graph would have auto-dismissed every
# Phase B contract opportunity.
KNOWN_DETECTORS = frozenset(
    {
        DETECTOR_SKILLS_NO_EVIDENCE,
        DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION,
        DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION,
        DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS,
        DETECTOR_LEGACY_NO_PRESERVATION,
        DETECTOR_DISCONNECTED_CLUSTERS,
    }
)

# A cluster opportunity's description starts with this prefix —
# the description IS the cluster fingerprint (its members), so a
# user who rewrites the description opts the opportunity out of
# auto-dismiss matching (it is left open).
CLUSTER_PREFIX = "Disconnected cluster of "

# Manual opportunities carry this detector value.
MANUAL_DETECTOR = "manual"


# --- Findings ---

Finding = dict


def _finding(
    *,
    opportunity_type: str,
    title: str,
    description: str,
    confidence: float,
    detector: str,
    source_type: str | None,
    source_id: int | None,
) -> Finding:
    return {
        "opportunity_type": opportunity_type,
        "title": title,
        "description": description,
        "confidence": confidence,
        "detector": detector,
        "source_type": source_type,
        "source_id": source_id,
        "evidence_id": None,
    }


def _finding_key(finding: Finding) -> tuple:
    """The identity of a finding. D1–D5 are keyed by detector +
    source record; D6 (clusters) has no single source record and
    is keyed by its canonical cluster description."""
    if finding["detector"] == DETECTOR_DISCONNECTED_CLUSTERS:
        return (
            finding["detector"],
            "GraphCluster",
            None,
            finding["description"],
        )
    return (
        finding["detector"],
        finding["source_type"],
        finding["source_id"],
        None,
    )


def _opportunity_key(opp: Opportunity) -> tuple:
    """The identity of a stored opportunity, in the same shape as
    _finding_key, so the two can be compared."""
    if opp.detector == DETECTOR_DISCONNECTED_CLUSTERS:
        return (opp.detector, "GraphCluster", None, opp.description)
    return (opp.detector, opp.source_type, opp.source_id, None)


# --- CRUD helpers ---

async def get_opportunity_or_404(
    db: AsyncSession, opportunity_id: int
) -> Opportunity:
    """Load an opportunity by id or raise 404."""
    result = await db.execute(
        select(Opportunity).where(Opportunity.id == opportunity_id)
    )
    opp = result.scalar_one_or_none()
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )
    return opp


async def assert_opportunity_access(
    db: AsyncSession, opp: Opportunity, user: User
) -> None:
    """Raise 403 unless the caller may read the opportunity's
    entity."""
    entity = await get_entity_or_404(db, opp.entity_id)
    assert_entity_access(entity, user)


async def assert_opportunity_owner(
    db: AsyncSession, opp: Opportunity, user: User
) -> None:
    """Raise 403 unless the caller owns the opportunity's
    entity."""
    entity = await get_entity_or_404(db, opp.entity_id)
    assert_entity_owner(entity, user)


async def create_opportunity(
    db: AsyncSession, user: User, payload: OpportunityCreate
) -> Opportunity:
    """Create an opportunity on an owned entity and write its
    audit entry (AT-407). The caller commits."""
    entity = await get_entity_or_404(db, payload.entity_id)
    assert_entity_owner(entity, user)
    if payload.evidence_id is not None:
        await get_record_or_404(db, EvidenceRecord, payload.evidence_id)
    opp = Opportunity(
        entity_id=payload.entity_id,
        title=payload.title,
        description=payload.description,
        opportunity_type=payload.opportunity_type.value,
        confidence=payload.confidence,
        status=payload.status.value,
        detector=payload.detector,
        source_type=payload.source_type,
        source_id=payload.source_id,
        evidence_id=payload.evidence_id,
    )
    db.add(opp)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_OPPORTUNITY_CREATED,
        entity_id=opp.entity_id,
        record_type="Opportunity",
        record_id=opp.id,
        actor=user.email,
        details={
            "opportunity_type": opp.opportunity_type,
            "confidence": opp.confidence,
            "detector": opp.detector,
            "source_type": opp.source_type,
            "source_id": opp.source_id,
        },
    )
    return opp


def _apply_patch(row: Opportunity, data: dict) -> None:
    """Apply a PATCH payload: only supplied, non-None values
    change (None means "no change", matching the repo's PATCH
    semantics)."""
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value)


async def update_opportunity(
    db: AsyncSession,
    user: User,
    opp: Opportunity,
    payload: OpportunityUpdate,
) -> Opportunity:
    """Update an opportunity. A status change writes an audit
    entry (AT-407). The caller commits."""
    old_status = opp.status
    data = payload.model_dump()
    # Enum values arrive as enum members; store plain strings.
    if data.get("opportunity_type") is not None:
        data["opportunity_type"] = data["opportunity_type"].value
    if data.get("status") is not None:
        data["status"] = data["status"].value
    _apply_patch(opp, data)
    if opp.status != old_status:
        log_event(
            db,
            event_type=EVENT_OPPORTUNITY_STATUS_CHANGED,
            entity_id=opp.entity_id,
            record_type="Opportunity",
            record_id=opp.id,
            actor=user.email,
            details={"from_status": old_status, "to_status": opp.status},
        )
    return opp


async def delete_opportunity(
    db: AsyncSession, opp: Opportunity
) -> None:
    """Delete an opportunity. The caller commits."""
    await db.delete(opp)


async def list_opportunities(
    db: AsyncSession,
    user: User,
    *,
    entity_id: int | None = None,
    status_filter: str | None = None,
    opportunity_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Opportunity]:
    """Opportunities on entities the caller may read (their own
    plus shared ones), optionally filtered."""
    ids = await accessible_entity_ids(db, user)
    stmt = select(Opportunity).where(Opportunity.entity_id.in_(ids))
    if entity_id is not None:
        entity = await get_entity_or_404(db, entity_id)
        assert_entity_access(entity, user)
        stmt = stmt.where(Opportunity.entity_id == entity_id)
    if status_filter is not None:
        stmt = stmt.where(Opportunity.status == status_filter)
    if opportunity_type is not None:
        stmt = stmt.where(
            Opportunity.opportunity_type == opportunity_type
        )
    stmt = (
        stmt.order_by(Opportunity.created_at, Opportunity.id)
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


# --- Detectors ---

async def _detect_skills_no_evidence(
    graph: GraphService, user: User, entity: Entity
) -> list[Finding]:
    """D1 — skills with no evidence (Sprint 3 graph query)."""
    skills = await graph.query_skills_no_evidence(user, entity.id)
    return [
        _finding(
            opportunity_type="Skill Opportunity",
            title=f"Skill with no evidence: {skill.name}",
            description=(
                f"The skill '{skill.name}' has no supporting "
                "evidence. Add evidence — a certificate, a project, "
                "a transcript — to make this capability verifiable."
            ),
            confidence=CONFIDENCE[DETECTOR_SKILLS_NO_EVIDENCE],
            detector=DETECTOR_SKILLS_NO_EVIDENCE,
            source_type="Skill",
            source_id=skill.id,
        )
        for skill in skills
    ]


async def _holder_has_evidence(
    graph: GraphService, node_id: int
) -> bool:
    """True when a holder node (Skill / Certification) has at
    least one outgoing HAS_EVIDENCE edge."""
    edges = await graph.repo.list_edges(
        from_node_id=node_id,
        relationship_type=GraphRelationshipType.HAS_EVIDENCE.value,
        limit=1,
    )
    return bool(edges)


async def _holder_has_other_connections(
    graph: GraphService,
    node_id: int,
    *,
    ownership_types: tuple[str, ...],
) -> bool:
    """True when a holder node has any edge beyond ownership
    (incoming OWNS / typed edge) and evidence (outgoing
    HAS_EVIDENCE). Any other edge — a manual relationship, a link
    to another record — counts as a connection."""
    pairs = await graph.repo.neighbors(node_id)
    for edge, _neighbor in pairs:
        is_ownership = (
            edge.relationship_type in ownership_types
            and edge.to_node_id == node_id
        )
        is_evidence = (
            edge.relationship_type
            == GraphRelationshipType.HAS_EVIDENCE.value
            and edge.from_node_id == node_id
        )
        if not is_ownership and not is_evidence:
            return True
    return False


async def _detect_skills_evidence_no_utilization(
    db: AsyncSession, graph: GraphService, user: User, entity: Entity
) -> list[Finding]:
    """D2 — skills with evidence but no utilization. See the
    module docstring for the honest operational definition: the
    system has no utilization data, so "no utilization" means the
    skill is verified (evidenced) but connected to nothing beyond
    ownership and evidence."""
    result = await db.execute(
        select(Skill).where(Skill.entity_id == entity.id)
    )
    findings: list[Finding] = []
    for skill in result.scalars().all():
        node = await graph.repo.get_node_by_source(
            GraphNodeType.SKILL.value, skill.id
        )
        if node is None:
            continue
        if not await _holder_has_evidence(graph, node.id):
            continue  # D1's territory — no evidence at all
        if await _holder_has_other_connections(
            graph,
            node.id,
            ownership_types=(
                GraphRelationshipType.OWNS.value,
                GraphRelationshipType.HAS_SKILL.value,
            ),
        ):
            continue
        findings.append(
            _finding(
                opportunity_type="Skill Opportunity",
                title=f"Verified skill with no connections: {skill.name}",
                description=(
                    f"The skill '{skill.name}' has evidence but is "
                    "not connected to any opportunity, asset, or "
                    "other value-producing relationship. Utilization "
                    "is not yet tracked in Engine v1 — this finding "
                    "means the skill is verified but isolated in "
                    "the graph."
                ),
                confidence=CONFIDENCE[
                    DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION
                ],
                detector=DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION,
                source_type="Skill",
                source_id=skill.id,
            )
        )
    return findings


async def _detect_certs_evidence_no_utilization(
    db: AsyncSession, graph: GraphService, user: User, entity: Entity
) -> list[Finding]:
    """D3 — certifications with evidence but no utilization (the
    same operational definition as D2, applied to
    certifications)."""
    result = await db.execute(
        select(Certification).where(Certification.entity_id == entity.id)
    )
    findings: list[Finding] = []
    for cert in result.scalars().all():
        node = await graph.repo.get_node_by_source(
            GraphNodeType.CERTIFICATION.value, cert.id
        )
        if node is None:
            continue
        if not await _holder_has_evidence(graph, node.id):
            continue
        if await _holder_has_other_connections(
            graph,
            node.id,
            ownership_types=(
                GraphRelationshipType.OWNS.value,
                GraphRelationshipType.HAS_CERTIFICATION.value,
            ),
        ):
            continue
        findings.append(
            _finding(
                opportunity_type="Knowledge Opportunity",
                title=f"Certified but unconnected: {cert.title}",
                description=(
                    f"The certification '{cert.title}' has evidence "
                    "but is not connected to any opportunity, asset, "
                    "or other value-producing relationship. "
                    "Utilization is not yet tracked in Engine v1 — "
                    "this finding means the certification is "
                    "evidenced but isolated in the graph."
                ),
                confidence=CONFIDENCE[
                    DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION
                ],
                detector=DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION,
                source_type="Certification",
                source_id=cert.id,
            )
        )
    return findings


async def _detect_knowledge_no_relationships(
    db: AsyncSession, graph: GraphService, user: User, entity: Entity
) -> list[Finding]:
    """D4 — knowledge assets whose only graph edge is the OWNS
    edge from their entity (zero edges counts too). An asset
    serving as a Skill / Certification's KnowledgeAsset
    representation has no graph edge to its holder, so it can
    still be flagged — see the module docstring."""
    entity_node = await graph.repo.get_node_by_source(
        GraphNodeType.ENTITY.value, entity.id
    )
    result = await db.execute(
        select(KnowledgeAsset).where(KnowledgeAsset.entity_id == entity.id)
    )
    findings: list[Finding] = []
    for asset in result.scalars().all():
        node = await graph.repo.get_node_by_source(
            GraphNodeType.KNOWLEDGE_ASSET.value, asset.id
        )
        if node is None:
            continue
        pairs = await graph.repo.neighbors(node.id)
        only_owns = all(
            edge.relationship_type
            == GraphRelationshipType.OWNS.value
            and entity_node is not None
            and edge.from_node_id == entity_node.id
            for edge, _neighbor in pairs
        )
        if not only_owns:
            continue
        is_legacy = asset.category == "Legacy Asset"
        findings.append(
            _finding(
                opportunity_type=(
                    "Asset Opportunity"
                    if is_legacy
                    else "Knowledge Opportunity"
                ),
                title=f"Knowledge asset with no relationships: {asset.title}",
                description=(
                    f"The knowledge asset '{asset.title}' has no "
                    "graph relationships beyond ownership. Connect "
                    "it — link evidence, relate it to a skill, or "
                    "use it in a project — or it stays invisible to "
                    "the Opportunity Engine."
                ),
                confidence=CONFIDENCE[
                    DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS
                ],
                detector=DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS,
                source_type="KnowledgeAsset",
                source_id=asset.id,
            )
        )
    return findings


async def _detect_legacy_no_preservation(
    graph: GraphService, user: User, entity: Entity
) -> list[Finding]:
    """D5 — legacy assets with no preservation status (Sprint 3
    graph query)."""
    assets = await graph.query_legacy_no_preservation_status(
        user, entity.id
    )
    return [
        _finding(
            opportunity_type="Legacy Opportunity",
            title=f"Legacy asset with no preservation status: {asset.title}",
            description=(
                f"The legacy asset '{asset.title}' has no "
                "preservation status. Set one so the system can "
                "tell whether this value is protected."
            ),
            confidence=CONFIDENCE[DETECTOR_LEGACY_NO_PRESERVATION],
            detector=DETECTOR_LEGACY_NO_PRESERVATION,
            source_type="LegacyAsset",
            source_id=asset.id,
        )
        for asset in assets
    ]


async def _detect_disconnected_clusters(
    graph: GraphService, user: User, entity: Entity
) -> list[Finding]:
    """D6 — connected components of the entity's graph that
    contain no entity node: nodes connected to each other but not
    to the ledger. Found by BFS over the graph layer."""
    nodes = await graph.list_accessible_nodes(user, limit=1000)
    entity_nodes: dict[int, object] = {}
    for node in nodes:
        if await graph.node_entity_id(node) == entity.id:
            entity_nodes[node.id] = node
    if not entity_nodes:
        return []
    edges = await graph.list_accessible_edges(user, limit=1000)
    adjacency: dict[int, set[int]] = {
        node_id: set() for node_id in entity_nodes
    }
    for edge in edges:
        if (
            edge.from_node_id in adjacency
            and edge.to_node_id in adjacency
        ):
            adjacency[edge.from_node_id].add(edge.to_node_id)
            adjacency[edge.to_node_id].add(edge.from_node_id)
    entity_node_id: int | None = None
    for node_id, node in entity_nodes.items():
        if (
            node.node_type == GraphNodeType.ENTITY.value
            and node.source_id == entity.id
        ):
            entity_node_id = node_id
            break
    findings: list[Finding] = []
    seen: set[int] = set()
    for node_id in sorted(adjacency):
        if node_id in seen:
            continue
        component: set[int] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        seen |= component
        if entity_node_id is not None and entity_node_id in component:
            continue
        members = sorted(
            (entity_nodes[nid].node_type, entity_nodes[nid].source_id)
            for nid in component
        )
        counts = Counter(node_type for node_type, _ in members)
        count_str = ", ".join(
            f"{count} {node_type}"
            for node_type, count in sorted(counts.items())
        )
        member_str = ", ".join(
            f"{node_type}#{source_id}" for node_type, source_id in members
        )
        description = (
            f"Disconnected cluster of {len(component)} nodes "
            f"({count_str}).\nMembers: {member_str}."
        )
        findings.append(
            _finding(
                opportunity_type="Relationship Opportunity",
                title=f"Disconnected cluster: {len(component)} nodes",
                description=description,
                confidence=CONFIDENCE[DETECTOR_DISCONNECTED_CLUSTERS],
                detector=DETECTOR_DISCONNECTED_CLUSTERS,
                source_type="GraphCluster",
                source_id=None,
            )
        )
    return findings


# --- Detection run ---

async def run_detection(
    db: AsyncSession,
    graph: GraphService,
    user: User,
    entity: Entity,
) -> dict:
    """Run all six detectors for one entity and reconcile with
    the opportunities already stored.

    Idempotent: a finding does not duplicate an open (detected or
    accepted) opportunity for the same detector + source record.
    A gap that closed dismisses its open opportunity
    automatically, with a note appended to the description and an
    audit entry — except a cluster opportunity whose description
    the user rewrote, which is left alone (the description is the
    cluster fingerprint and can no longer be matched). Only
    opportunities created by the six detectors are ever
    auto-dismissed: an opportunity created by an importer (a
    foreign detector value) is owned by its importer, and a
    detection run must never close it.

    Returns {"created": [...], "dismissed": [...]}. The caller
    commits."""
    findings: list[Finding] = []
    findings.extend(
        await _detect_skills_no_evidence(graph, user, entity)
    )
    findings.extend(
        await _detect_skills_evidence_no_utilization(db, graph, user, entity)
    )
    findings.extend(
        await _detect_certs_evidence_no_utilization(db, graph, user, entity)
    )
    findings.extend(
        await _detect_knowledge_no_relationships(db, graph, user, entity)
    )
    findings.extend(
        await _detect_legacy_no_preservation(graph, user, entity)
    )
    findings.extend(
        await _detect_disconnected_clusters(graph, user, entity)
    )
    current_keys = {_finding_key(f) for f in findings}

    result = await db.execute(
        select(Opportunity).where(
            Opportunity.entity_id == entity.id,
            Opportunity.status.in_(list(OPEN_STATUSES)),
            Opportunity.detector != MANUAL_DETECTOR,
        )
    )
    open_opps = list(result.scalars().all())
    open_by_key = {_opportunity_key(opp): opp for opp in open_opps}

    created: list[Opportunity] = []
    for finding in findings:
        if _finding_key(finding) in open_by_key:
            continue
        opp = Opportunity(
            entity_id=entity.id,
            title=finding["title"],
            description=finding["description"],
            opportunity_type=finding["opportunity_type"],
            confidence=finding["confidence"],
            status=OpportunityStatus.DETECTED.value,
            detector=finding["detector"],
            source_type=finding["source_type"],
            source_id=finding["source_id"],
            evidence_id=finding["evidence_id"],
        )
        db.add(opp)
        created.append(opp)
    await db.flush()
    for opp in created:
        log_event(
            db,
            event_type=EVENT_OPPORTUNITY_CREATED,
            entity_id=opp.entity_id,
            record_type="Opportunity",
            record_id=opp.id,
            actor=user.email,
            details={
                "opportunity_type": opp.opportunity_type,
                "confidence": opp.confidence,
                "detector": opp.detector,
                "source_type": opp.source_type,
                "source_id": opp.source_id,
            },
        )

    dismissed: list[Opportunity] = []
    today = date.today().isoformat()
    for opp in open_opps:
        if opp.detector not in KNOWN_DETECTORS:
            # Not a detector finding — owned by its importer (e.g.
            # the Phase B contract import). A detection run must
            # never close it: the detectors cannot know whether
            # the imported gap closed.
            continue
        key = _opportunity_key(opp)
        if key in current_keys:
            continue
        if opp.detector == DETECTOR_DISCONNECTED_CLUSTERS:
            if not (opp.description or "").startswith(CLUSTER_PREFIX):
                # The user rewrote the description — the cluster
                # fingerprint is gone, so leave it alone.
                continue
        opp.status = OpportunityStatus.DISMISSED.value
        opp.description = (
            (opp.description or "")
            + f"\n[Auto-dismissed: the gap this opportunity described "
            f"was no longer found on {today}.]"
        )
        dismissed.append(opp)
        log_event(
            db,
            event_type=EVENT_OPPORTUNITY_AUTO_DISMISSED,
            entity_id=opp.entity_id,
            record_type="Opportunity",
            record_id=opp.id,
            actor=user.email,
            details={
                "reason": "gap no longer found",
                "detector": opp.detector,
                "source_type": opp.source_type,
                "source_id": opp.source_id,
            },
        )
    return {"created": created, "dismissed": dismissed}
