"""OmniSight -> Freedom GP$ connector, Phase B (EO 007A / Integration
Sprint B): Contract Intel -> Opportunity Records.

The second span of the missing wire: the OmniSight NexIntel loop
already publishes a nonprofit-focused SAM.gov watch as public JSON
(data/sam_nonprofit.json in the omnisight27/index.html repo). This
module loads that watch into the engine through the existing write
paths only:

- one KnowledgeAsset per solicitation (category "Opportunity") via
  the vault endpoint's create path — creating the asset also
  creates its graph node (type KnowledgeAsset) plus the OWNS edge
  from the entity, by the Sprint 3 auto-sync. No parallel graph
  path is built.
- one Opportunity per solicitation via opportunity_service.
  create_opportunity, with status "detected". No outcome exists
  until someone acts: Applied / Not Applied is the user's later
  decision through the existing opportunity status flow
  (accepted = pursuing / applied, dismissed = not pursuing,
  realized = awarded).

How the loop produces the feed (verified 2026-11-21 against
Scripts/nexintel.py and the live file): the API takes ONE NAICS
code per request and a non-associated key gets ~10 requests/day,
while the loop runs ~2x/day, so each run queries ONE code,
rotating through the 15 NONPROFIT_NAICS codes (used-merchandise
retail, materials recovery, waste collection, community and
family services, vocational rehab, and more), and merges the
results into a persistent ledger (data/sam_nonprofit.json,
committed by the loop like history.json). A 30-day window per
code means the ~4-day rotation never misses a posting.
Past-deadline rows are pruned on every merge; a failed fetch
keeps the old ledger. The ledger is capped at 100 rows, sorted
by posted date, newest first. `next_index` tracks the rotation.
Relevance is matched two ways by the loop itself: NAICS codes
for the work a nonprofit like NicNacs does, and keywords in the
solicitation title (nonprofit_match returns the reason, e.g.
"NAICS 562111 — Solid waste collection").

What the live file actually contains (verified 2026-11-21): 8
rows, all nonprofit=true with a nonprofit_match reason (7x NAICS
562111 solid waste collection, 1x NAICS 562998 other waste
management), posted 2026-11-13 -> 2026-11-19, deadlines
2026-11-27 -> 2026-12-14, states "?" (4), IA, OR, WA, CF-BGF.

Entity matching — the rule, stated plainly. The feed is
pre-filtered by the loop: every row is a solicitation the loop
already judged nonprofit-relevant (nonprofit=true with a
nonprofit_match reason). The import trusts that match — it does
not re-implement NAICS matching — and preserves the reason
verbatim on the asset and the opportunity, so the match basis
is auditable. The operator selects the target entity (the
--entity-id the CLI requires): the natural owner of this feed
is a nonprofit entity (NicNacs); a Long Solutions (business)
entity would use the same feed only if the operator judges the
solicitations relevant to it. The engine holds no real NicNacs
or Long Solutions entities yet — like Phase A, the operator
must create the user + entity first; the CLI never creates
them. State is recorded as-is ("?" means the loop could not
determine the place of performance); there is no geographic
filtering at import time — a national solicitation with state
"?" may still be pursuable, and the user decides by accepting
or dismissing. The import does not decide whether the entity
can perform the work; that judgment is the user's, recorded as
the opportunity's status.

Two design gaps, resolved from the code (not guessed):

1. OpportunityType has no "Contract Opportunity". The Sprint 4
   enum is Skill / Asset / Knowledge / Relationship / Legacy,
   and the create path validates against it, so a contract
   opportunity cannot be typed as one without inventing a new
   model — which EO 007A forbids. The least-bad existing
   representation is "Asset Opportunity": a contract award is a
   future financial asset — a revenue stream the entity can win
   (the D4 detector already maps asset-like records to "Asset
   Opportunity"). The record is unambiguous anyway: detector
   "contract_intel_sam_gov", source_type "KnowledgeAsset" with
   source_id the asset, content_type "sam-gov-contract", and a
   title and description that say plainly it is a SAM.gov
   solicitation. The gap is real and is flagged here: a future
   migration should add "Contract Opportunity" to
   OpportunityType (the same pattern as the A.5 scope's
   migration 0008 for the registry source field). Until then,
   queries for contract opportunities filter by detector, not
   by type.

2. KnowledgeCategory had no "Opportunity" either — but unlike
   the OpportunityType enum, this one the mapping requires:
   EO 007A maps Contract Intel to Knowledge Asset (Category:
   Opportunity). Following Phase A's precedent (which added
   "Prediction" the same way), the category was added to the
   enum in app/models/vault.py. It is an enum member, not a new
   model.

The Architect's new graph node types (Prediction, Contract,
Grant, Alert, Market Observation, Economic Indicator) and
relationships (PREDICTS, RESOLVES_TO, MATCHES, QUALIFIES_FOR,
GENERATES, INFLUENCES, OBSERVED_AS) are a graph-taxonomy
extension for a later phase — Phase B uses the existing
KnowledgeAsset node + OWNS edge, which is the verified
auto-sync behavior, exactly as Phase A did.

Confidence: the detector's certainty in the finding (0-1), not
a value estimate. 0.85 when the row carries a nonprofit_match
(the loop's own match — the same confidence as the D4
knowledge-asset detector), 0.5 when it does not (the loop did
not match the row; the record is still imported because the
file IS the nonprofit ledger, but it is marked as weak).
These confidences are illustrative, like the Sprint 4 ones.

Idempotency: every solicitation carries a natural key in the
asset's `source` field — "contract:sam-gov:{notice_id}". The
notice_id is the SAM.gov notice ID, unique per solicitation,
and the loop itself dedups by it. Re-running the import never
duplicates anything: an existing asset is found by its source
key, and an opportunity is found by (detector, source_type,
source_id) in ANY status — a dismissed opportunity is never
recreated, because the dismissal was the user's decision. If
a re-run finds the row's facts changed (e.g. a deadline
extended on re-posting), the asset description is updated
through the existing update path (version increments); the
opportunity is left alone. Rows that disappear from the file
(the loop prunes past-deadline rows on every merge) are NOT
auto-dismissed — the import never deletes or closes user
records — but they are counted as stale_assets_not_in_feed so
the operator can see them.

Deliberately NOT in Phase B (EO 007A: do not add features,
scores, or engines): the general SAM.gov feed (the loop's
d['opportunities'] — 25 solicitations per run, not the
nonprofit ledger) is a separate feed and is not imported here;
grants are Phase C; the "Contract Opportunity" node type and
OpportunityType value are future migrations, not this phase;
no expiry auto-dismiss; no new detectors.

Read-only on the live data: the import fetches the public JSON
output file. It never calls the SAM.gov API and never touches
the loop's keys (they stay in GitHub Secrets).
"""

from __future__ import annotations

import json
import urllib.request

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.vault import (
    create_knowledge_asset as create_knowledge_asset_endpoint,
)
from app.api.vault import (
    update_knowledge_asset as update_knowledge_asset_endpoint,
)
from app.models.entity import Entity, EntityType
from app.models.graph import GraphRelationshipType
from app.models.opportunity import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
)
from app.models.user import User
from app.models.vault import KnowledgeAsset, KnowledgeCategory
from app.schemas.opportunity import OpportunityCreate
from app.schemas.vault import KnowledgeAssetCreate, KnowledgeAssetUpdate
from app.services.entity_resolution import (
    link_asset_to_org,
    resolve_or_create,
)
from app.services.opportunity_service import create_opportunity

FEED_URL = (
    "https://raw.githubusercontent.com/omnisight27/index.html/main/"
    "data/sam_nonprofit.json"
)

# The detector value stored on every opportunity this import
# creates. Queries for contract opportunities filter by this,
# not by opportunity_type (see the module docstring: the enum
# cannot represent a contract opportunity yet).
DETECTOR = "contract_intel_sam_gov"

# The least-bad existing opportunity type (see the module
# docstring): a contract award is a future financial asset — a
# revenue stream the entity can win. The enum has no "Contract
# Opportunity" value; queries for contract opportunities filter
# by detector, not by type.
OPPORTUNITY_TYPE = OpportunityType.ASSET

CONTENT_TYPE = "sam-gov-contract"

# Confidence (the detector's certainty, 0-1 — illustrative, like
# the Sprint 4 detector confidences): the loop's own nonprofit
# match is the basis; a row without one is marked weak.
CONFIDENCE_MATCHED = 0.85
CONFIDENCE_UNMATCHED = 0.5


def contract_source_key(notice_id: str) -> str:
    """The natural key of one solicitation, stored in the asset's
    `source` field. The notice_id is the SAM.gov notice ID —
    unique per solicitation, and the loop itself dedups by it."""
    return f"contract:sam-gov:{notice_id}"


def fetch_feed(url: str = FEED_URL) -> dict:
    """Fetch the live feed file (read-only). The loop's keys stay
    in GitHub Secrets — this reads the public output file, never
    the SAM.gov API."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "Freedom-GP$-contract-import/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_feed(source: str) -> dict:
    """Load sam_nonprofit.json from a URL or a local file path."""
    if source.startswith(("http://", "https://")):
        return fetch_feed(source)
    with open(source, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _truncate(text: str, limit: int = 200) -> str:
    """Titles are capped at 200 characters by the schemas; the
    full title lives in the description."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _row_description(row: dict, *, for_opportunity: bool) -> str:
    """The record's description: the solicitation's facts, so the
    record stands alone without the source file. The match reason
    is preserved verbatim — it is the loop's own explanation of
    why this solicitation is nonprofit-relevant."""
    match = row.get("nonprofit_match")
    lines = [
        f"Title: {row.get('title') or '?'}",
        f"Agency: {row.get('agency') or '?'}",
        f"Type: {row.get('type') or '?'}",
        f"Posted: {row.get('posted') or '?'}",
        f"Response deadline: {row.get('deadline') or '?'}",
        f"NAICS: {row.get('naics') or '?'}",
        f"Place of performance (state): {row.get('state') or '?'}",
        f"Notice ID: {row.get('notice_id') or '?'}",
        "Nonprofit match: "
        + (str(match) if match else "none recorded by the loop"),
        "",
        "Source: SAM.gov nonprofit watch "
        "(data/sam_nonprofit.json), via the OmniSight NexIntel "
        "loop. The loop queries one nonprofit NAICS code per run "
        "on rotation; this row is the solicitation as published "
        "by SAM.gov, normalized by the loop.",
    ]
    if for_opportunity:
        lines.append(
            "Outcome tracking: this opportunity starts as "
            "detected. Applied / Not Applied is the user's later "
            "decision — accept to pursue (applied), dismiss to "
            "pass, realized when awarded. No outcome exists until "
            "someone acts."
        )
    return "\n".join(lines)


async def find_asset_by_source(
    db: AsyncSession, entity_id: int, source_key: str
) -> KnowledgeAsset | None:
    result = await db.execute(
        select(KnowledgeAsset).where(
            KnowledgeAsset.entity_id == entity_id,
            KnowledgeAsset.source == source_key,
        )
    )
    return result.scalar_one_or_none()


async def find_opportunity_for_asset(
    db: AsyncSession, entity_id: int, asset_id: int
) -> Opportunity | None:
    """Find the contract opportunity for one asset, in ANY status.
    A dismissed opportunity is never recreated — the dismissal
    was the user's decision."""
    result = await db.execute(
        select(Opportunity).where(
            Opportunity.entity_id == entity_id,
            Opportunity.detector == DETECTOR,
            Opportunity.source_type == "KnowledgeAsset",
            Opportunity.source_id == asset_id,
        )
    )
    return result.scalar_one_or_none()


async def import_contract_intel(
    db: AsyncSession,
    user: User,
    entity: Entity,
    feed: dict,
    *,
    source_url: str = FEED_URL,
) -> dict:
    """Import one sam_nonprofit.json file into the Opportunity
    Engine. Idempotent: re-running with the same (or a fresher)
    file never duplicates an asset or an opportunity, updates
    an asset whose facts changed, and leaves dismissed
    opportunities alone. Returns the counts."""
    counts = {
        "rows_seen": 0,
        "with_match": 0,
        "without_match": 0,
        "skipped_incomplete": 0,
        "skipped_duplicate": 0,
        "knowledge_assets_created": 0,
        "assets_updated": 0,
        "opportunities_created": 0,
        "stale_assets_not_in_feed": 0,
        # Phase C.1 — Entity Resolution: how each row's agency
        # resolved (L1/L2/L3 auto-attached, L4 queued a review,
        # NEW created a shared org entity).
        "resolution_l1": 0,
        "resolution_l2": 0,
        "resolution_l3": 0,
        "resolution_l4": 0,
        "resolution_new": 0,
        "resolution_queued": 0,
        "issued_by_edges_created": 0,
    }
    rows = feed.get("rows") or []
    seen_keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            counts["skipped_incomplete"] += 1
            continue
        counts["rows_seen"] += 1
        notice_id = str(row.get("notice_id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not notice_id or notice_id == "?" or not title or title == "?":
            # No stable key (or no title) — the record cannot be
            # identified or displayed. Skipped, counted, never
            # guessed.
            counts["skipped_incomplete"] += 1
            continue
        match = row.get("nonprofit_match")
        if match:
            counts["with_match"] += 1
        else:
            counts["without_match"] += 1
        confidence = (
            CONFIDENCE_MATCHED if match else CONFIDENCE_UNMATCHED
        )
        source_key = contract_source_key(notice_id)
        if source_key in seen_keys:
            # The same solicitation twice in one file (the loop
            # dedups by notice id, so this should not happen, but
            # a stale duplicate must not overwrite the first
            # row's facts — e.g. revert an updated deadline).
            counts["skipped_duplicate"] += 1
            continue
        seen_keys.add(source_key)
        created_anything = False

        asset = await find_asset_by_source(db, entity.id, source_key)
        description = _row_description(row, for_opportunity=False)
        if asset is None:
            asset = await create_knowledge_asset_endpoint(
                KnowledgeAssetCreate(
                    entity_id=entity.id,
                    title=_truncate(title),
                    description=description,
                    category=KnowledgeCategory.OPPORTUNITY,
                    content_type=CONTENT_TYPE,
                    confidence=confidence,
                    source=source_key,
                ),
                current_user=user,
                db=db,
            )
            counts["knowledge_assets_created"] += 1
            created_anything = True
        elif asset.description != description:
            # The solicitation's facts changed on re-posting
            # (e.g. a deadline extended): update through the
            # existing path (version increments). The
            # opportunity is left alone.
            await update_knowledge_asset_endpoint(
                asset.id,
                KnowledgeAssetUpdate(description=description),
                current_user=user,
                db=db,
            )
            counts["assets_updated"] += 1
            created_anything = True

        opportunity = await find_opportunity_for_asset(
            db, entity.id, asset.id
        )
        if opportunity is None:
            await create_opportunity(
                db,
                user,
                OpportunityCreate(
                    entity_id=entity.id,
                    title=_truncate(f"Contract opportunity: {title}"),
                    description=_row_description(
                        row, for_opportunity=True
                    ),
                    opportunity_type=OPPORTUNITY_TYPE,
                    confidence=confidence,
                    status=OpportunityStatus.DETECTED,
                    detector=DETECTOR,
                    source_type="KnowledgeAsset",
                    source_id=asset.id,
                ),
            )
            counts["opportunities_created"] += 1
            created_anything = True
        # Phase C.1 — Entity Resolution: the agency becomes a
        # real org node (shared, type business) and the asset
        # gains an ISSUED_BY edge to it. A non-OWNS edge also
        # closes the D4 "no relationships" gap for this asset.
        agency = str(row.get("agency") or "").strip()
        if agency and agency != "?":
            resolution = await resolve_or_create(
                db,
                name=agency,
                entity_type=EntityType.BUSINESS.value,
                context={
                    "state": str(row.get("state") or "").strip(),
                    "naics": str(row.get("naics") or "").strip(),
                },
                owner_id=None,
                user=user,
            )
            counts[f"resolution_{resolution.layer.lower()}"] += 1
            if resolution.review_id is not None:
                counts["resolution_queued"] += 1
            if await link_asset_to_org(
                db,
                asset=asset,
                org=resolution.entity,
                relationship_type=GraphRelationshipType.ISSUED_BY.value,
                confidence=resolution.confidence,
            ):
                counts["issued_by_edges_created"] += 1
        if not created_anything:
            counts["skipped_duplicate"] += 1
        await db.commit()

    # Rows the loop pruned (past deadline) are not auto-dismissed
    # — the import never closes user records — but they are
    # counted so the operator can see them.
    existing = (
        await db.execute(
            select(KnowledgeAsset).where(
                KnowledgeAsset.entity_id == entity.id,
                KnowledgeAsset.content_type == CONTENT_TYPE,
            )
        )
    ).scalars().all()
    counts["stale_assets_not_in_feed"] = sum(
        1 for a in existing if (a.source or "") not in seen_keys
    )
    return counts
