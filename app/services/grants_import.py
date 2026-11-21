"""OmniSight -> Freedom GP$ connector, Phase C part 1 (EO 007A /
Integration Sprint C): Grants -> Opportunity Records.

The third span of the missing wire: the OmniSight site publishes a
curated grants list for NicNacs (grants-data.js in the
omnisight27/index.html repo). This module loads that list into the
engine through the existing write paths only:

- one KnowledgeAsset per grant (category "Opportunity") via the
  vault endpoint's create path — creating the asset also creates
  its graph node (type KnowledgeAsset) plus the OWNS edge from
  the entity, by the Sprint 3 auto-sync. No parallel graph path
  is built.
- one Opportunity per pursuable grant via opportunity_service.
  create_opportunity, with status "detected" — except a grant
  already submitted, which imports as "accepted" (the
  application is in; accepted = pursuing / applied, per the
  Phase B mapping). The outcome chain is Opportunity ->
  Application -> Award / Rejection -> Validation Evidence; no
  outcome exists until the funder decides.

What the live file actually contains (verified 2026-11-21): two
arrays in one JS file. `window.NICNACS_GRANTS` — 20 grants
curated for NicNacs (uniform fields: name, funder, level, tier,
amount, deadline, deadlineISO, status, funds, eligibility, url,
notes; tiers HOT 3, WARM 2, COOL 8, COLD 6, SUBMITTED 1). And
`window.GRANT_DB` — 40 catalog grants for a public search tool
(uniform fields: name, funder, level, categories, states,
nonprofit_eligible, forprofit_eligible, deadline, amount, url,
blurb; nonprofit_eligible true 15, false 25; no deadlineISO).
Three grants appear in both arrays with the same funder (USDA
RBDG, USDA Community Facilities, Walmart Spark Good) — the same
real-world grant listed twice, so the tiered NicNacs entry wins
and the catalog duplicate is skipped.

Freshness (honest): the file is a curated snapshot, not a live
feed. Research pass 2026-10-30, 20 verification searches, funder
pages fetched directly — and static since. Three NicNacs
deadlines in it have already passed (Josephine County Oct 30 —
submitted, awaiting the board decision; USDA RBDG Jun 30 —
FY2026 closed; Oregon DEQ CERTA Nov 19). A live grants feed
(Grants.gov / SAM.gov assistance listings) is a later phase, not
this one; the import records the snapshot as it stands and
counts past-deadline entries so the operator can see them.

Entity matching — the rule, stated plainly. The file is the
NicNacs grants page (its own header carries an entity firewall:
nothing on the page may mention Long Solutions or any other
company), so the natural owner is a nonprofit entity (NicNacs).
The operator selects the target entity (the --entity-id the CLI
requires). Eligibility is judged against the record itself: a
catalog grant with nonprofit_eligible false is a grant this
entity cannot apply for — it is still imported as a Knowledge
Asset (the page lists it for a reason, and the knowledge is
real), but no Opportunity is created for it, and the reason is
recorded in the asset description. A business-entity variant
would flip the check to forprofit_eligible; that variant is out
of scope for this phase. The import does not decide whether the
entity will win, or whether it should apply; that judgment is
the user's, recorded as the opportunity's status.

Identity and entity resolution — Phase C.1. The import's
source key ("grant:{funder-slug}:{name-slug}") still
identifies the GRANT asset, but the FUNDER is now resolved
through the Phase C.1 engine (app/services/entity_resolution.py):
the funder name is normalized and matched (L1 exact, L2
normalized, L3 alias, L4 context — queue only), the funder
becomes a shared org node, and the asset gains a FUNDED_BY
edge to it. Two spellings of the same funder that slug
differently still produce different asset keys — the grant
identity is exact, on the slugged text — but the funder
identity is now resolved, so the same organization no longer
fragments into separate text mentions. The remaining gap:
grant NAME variants (two differently-worded names for the
same grant) are still distinct assets; only the funder is
resolved.

Two design gaps, resolved from the code (not guessed):

1. OpportunityType has no "Grant Opportunity". The Sprint 4
   enum is Skill / Asset / Knowledge / Relationship / Legacy,
   and the create path validates against it, so a grant
   opportunity cannot be typed as one without inventing a new
   model — which EO 007A forbids. The least-bad existing
   representation is "Asset Opportunity", the same one Phase B
   chose for contracts: a grant award is a future financial
   asset — funding the entity can win. The record is
   unambiguous anyway: detector "grants_curated_nicnacs",
   source_type "KnowledgeAsset" with source_id the asset,
   content_type "grant", and a title and description that say
   plainly it is a grant. The gap is real and is flagged here:
   a future migration should add "Grant Opportunity" (and
   "Contract Opportunity") to OpportunityType (the same pattern
   as the A.5 scope's migration 0008 for the registry source
   field). Until then, queries for grant opportunities filter
   by detector, not by type.

2. KnowledgeCategory already has "Opportunity" (added by
   Phase B) — no new category is needed. Grants use it with
   content_type "grant", which is what distinguishes them from
   the Phase B contract assets (content_type
   "sam-gov-contract").

Confidence: the tier is the curator's fit signal, so the
confidence follows it (illustrative, like the Sprint 4 detector
confidences): HOT 0.9, WARM 0.8, SUBMITTED 0.85, COOL 0.6,
COLD 0.4; a catalog grant explicitly nonprofit-eligible 0.7.
A grant the entity cannot apply for gets no opportunity at all
rather than a low-confidence one — a record the user cannot act
on is not an opportunity.

Idempotency: every grant carries a natural key in the asset's
`source` field — "grant:{funder-slug}:{name-slug}". The tiered
entry wins over a catalog duplicate of the same grant (same
funder, same name). Re-running the import never duplicates
anything: an existing asset is found by its source key, and an
opportunity is found by (detector, source_type, source_id) in
ANY status — a dismissed opportunity is never recreated,
because the dismissal was the user's decision. If a re-run
finds the entry's facts changed, the asset description is
updated through the existing update path (version increments);
the opportunity is left alone. Entries that disappear from the
file are NOT auto-dismissed — the import never deletes or
closes user records — but they are counted as
stale_assets_not_in_feed so the operator can see them.

Deliberately NOT in Phase C part 1 (EO 007A: do not add
features, scores, or engines): a live grants feed; a
business-entity eligibility variant; the "Grant Opportunity"
node type and OpportunityType value (future migrations, not
this phase); no expiry auto-dismiss; no new detectors (the
learning pass in part 2 runs the existing ones).

Read-only on the live data: the import fetches the public JS
file. It never calls any grants API and never touches the
loop's keys (they stay in GitHub Secrets).
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date
from urllib.parse import urlparse

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
    "grants-data.js"
)

# The detector value stored on every opportunity this import
# creates. Queries for grant opportunities filter by this, not by
# opportunity_type (see the module docstring: the enum cannot
# represent a grant opportunity yet).
DETECTOR = "grants_curated_nicnacs"

# The least-bad existing opportunity type (see the module
# docstring): a grant award is a future financial asset —
# funding the entity can win. The enum has no "Grant
# Opportunity" value; queries for grant opportunities filter by
# detector, not by type.
OPPORTUNITY_TYPE = OpportunityType.ASSET

CONTENT_TYPE = "grant"

# Confidence (the curator's fit signal, 0-1 — illustrative,
# like the Sprint 4 detector confidences). A grant the entity
# cannot apply for gets no opportunity at all rather than a
# low-confidence one.
CONFIDENCE_BY_TIER = {
    "HOT": 0.9,
    "WARM": 0.8,
    "SUBMITTED": 0.85,
    "COOL": 0.6,
    "COLD": 0.4,
}
CONFIDENCE_CATALOG_ELIGIBLE = 0.7

# The file is a curated snapshot, researched 2026-10-30 — not a
# live feed. Recorded on every asset so the staleness is
# auditable.
SOURCE_NOTE = (
    "Source: curated grants list (grants-data.js), research pass "
    "2026-10-30 — a static snapshot, not a live feed."
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """A URL-safe slug for a funder or grant name (the natural-key
    component). Empty input slugs to "unknown" so the key is
    never empty."""
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return slug or "unknown"


def grant_source_key(name: str, funder: str) -> str:
    """The natural key of one grant, stored in the asset's
    `source` field. The same real-world grant can appear in both
    arrays of the file (same funder, same name) — the key is
    identical for both, so the tiered entry wins and the catalog
    duplicate is skipped."""
    return f"grant:{_slug(funder)}:{_slug(name)}"


def parse_feed(text: str) -> dict:
    """Parse grants-data.js: a JS file carrying two arrays,
    `window.NICNACS_GRANTS` (the 20 tiered NicNacs grants) and
    `window.GRANT_DB` (the 40 catalog grants). Returns
    {"nicnacs": [...], "catalog": [...]}. Raises ValueError when
    either array cannot be found or parsed — a malformed feed is
    never half-imported."""
    feed: dict[str, list] = {}
    for var, key in (
        ("window.NICNACS_GRANTS", "nicnacs"),
        ("window.GRANT_DB", "catalog"),
    ):
        match = re.search(
            re.escape(var) + r"\s*=\s*(\[.*?\n\]);", text, re.S
        )
        if match is None:
            raise ValueError(f"grants feed has no {var} array")
        try:
            feed[key] = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"grants feed {var} array is not valid JSON: {exc}"
            ) from exc
    return feed


def fetch_feed(url: str = FEED_URL) -> dict:
    """Fetch the live grants file (read-only). The loop's keys
    stay in GitHub Secrets — this reads the public file, never
    any grants API."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "Freedom-GP$-grants-import/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return parse_feed(response.read().decode("utf-8"))


def load_feed(source: str) -> dict:
    """Load grants-data.js from a URL or a local file path."""
    if source.startswith(("http://", "https://")):
        return fetch_feed(source)
    with open(source, "r", encoding="utf-8") as handle:
        return parse_feed(handle.read())


def _truncate(text: str, limit: int = 200) -> str:
    """Titles are capped at 200 characters by the schemas; the
    full title lives in the description."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _is_past_deadline(entry: dict) -> bool:
    """True when the entry carries a deadlineISO in the past.
    Past-deadline entries are still imported (a submitted
    application lives past its deadline) — they are counted, and
    the fact is recorded in the description."""
    deadline_iso = str(entry.get("deadlineISO") or "").strip()
    if not deadline_iso:
        return False
    return deadline_iso < date.today().isoformat()


def _entry_description(entry: dict, *, for_opportunity: bool) -> str:
    """The record's description: the grant's facts, so the record
    stands alone without the source file."""
    name = entry.get("name") or "?"
    funder = entry.get("funder") or "?"
    lines = [
        f"Grant: {name}",
        f"Funder: {funder}",
        f"Level: {entry.get('level') or '?'}",
    ]
    if entry.get("tier") is not None:
        lines.append(f"Tier: {entry.get('tier')}")
    if entry.get("amount") is not None:
        lines.append(f"Amount: {entry.get('amount')}")
    deadline = entry.get("deadline") or "?"
    deadline_iso = str(entry.get("deadlineISO") or "").strip()
    if deadline_iso:
        lines.append(f"Deadline: {deadline} (ISO {deadline_iso})")
    else:
        lines.append(f"Deadline: {deadline}")
    if _is_past_deadline(entry):
        lines.append(
            "Note: the deadline has passed. The grant is still "
            "imported — a submitted application lives past its "
            "deadline, and a closed round may reopen."
        )
    if entry.get("status") is not None:
        lines.append(f"Status: {entry.get('status')}")
    if entry.get("funds") is not None:
        lines.append(f"Funds: {entry.get('funds')}")
    if entry.get("eligibility") is not None:
        lines.append(f"Eligibility: {entry.get('eligibility')}")
    if entry.get("categories") is not None:
        lines.append(
            "Categories: " + ", ".join(entry.get("categories") or [])
        )
    if entry.get("states") is not None:
        lines.append("States: " + ", ".join(entry.get("states") or []))
    if entry.get("nonprofit_eligible") is not None:
        lines.append(
            "Nonprofit eligible: "
            f"{entry.get('nonprofit_eligible')}"
        )
    if entry.get("forprofit_eligible") is not None:
        lines.append(
            "For-profit eligible: "
            f"{entry.get('forprofit_eligible')}"
        )
    if entry.get("blurb") is not None:
        lines.append(f"About: {entry.get('blurb')}")
    if entry.get("notes") is not None:
        lines.append(f"Notes: {entry.get('notes')}")
    lines.append(f"URL: {entry.get('url') or 'unverified'}")
    lines.append("")
    lines.append(SOURCE_NOTE)
    if for_opportunity:
        if (entry.get("tier") or "") == "SUBMITTED":
            lines.append(
                "Outcome tracking: the application is already in "
                "(status accepted). The funder's decision — Award "
                "or Rejection — is the outcome to record when it "
                "arrives, and it becomes Validation Evidence."
            )
        else:
            lines.append(
                "Outcome tracking: this opportunity starts as "
                "detected. Application / Award / Rejection is the "
                "user's later action — accept to pursue, dismiss "
                "to pass, realized when awarded. No outcome "
                "exists until someone acts."
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
    """Find the grant opportunity for one asset, in ANY status.
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


def _opportunity_eligible(entry: dict) -> bool:
    """Whether this entry is an opportunity for a nonprofit
    entity. Tiered NicNacs entries are eligible by construction
    (the list was curated for NicNacs). A catalog entry is
    eligible only when it says so — nonprofit_eligible false
    means the entity cannot apply, and no opportunity is
    created for it."""
    if entry.get("tier") is not None:
        return True
    return entry.get("nonprofit_eligible") is True


def _confidence(entry: dict) -> float:
    """The curator's fit signal for this entry (illustrative)."""
    tier = entry.get("tier")
    if tier is not None:
        return CONFIDENCE_BY_TIER.get(tier, 0.6)
    return CONFIDENCE_CATALOG_ELIGIBLE


async def import_grants(
    db: AsyncSession,
    user: User,
    entity: Entity,
    feed: dict,
    *,
    source_url: str = FEED_URL,
) -> dict:
    """Import one grants-data.js file into the Opportunity
    Engine. Idempotent: re-running with the same (or a fresher)
    file never duplicates an asset or an opportunity, updates an
    asset whose facts changed, and leaves dismissed opportunities
    alone. Returns the counts."""
    counts = {
        "entries_seen": 0,
        "nicnacs_seen": 0,
        "catalog_seen": 0,
        "skipped_incomplete": 0,
        "skipped_duplicate": 0,
        "skipped_not_nonprofit_eligible": 0,
        "knowledge_assets_created": 0,
        "assets_updated": 0,
        "opportunities_created": 0,
        "opportunities_accepted": 0,
        "past_deadline": 0,
        "stale_assets_not_in_feed": 0,
        # Phase C.1 — Entity Resolution: how each entry's
        # funder resolved (L1/L2/L3 auto-attached, L4 queued a
        # review, NEW created a shared org entity).
        "resolution_l1": 0,
        "resolution_l2": 0,
        "resolution_l3": 0,
        "resolution_l4": 0,
        "resolution_new": 0,
        "resolution_queued": 0,
        "funded_by_edges_created": 0,
    }
    seen_keys: set[str] = set()
    # The tiered NicNacs entries are processed first: when the
    # same real-world grant appears in both arrays, the curated
    # entry wins and the catalog duplicate is skipped.
    for section in ("nicnacs", "catalog"):
        for entry in feed.get(section) or []:
            if not isinstance(entry, dict):
                counts["skipped_incomplete"] += 1
                continue
            counts["entries_seen"] += 1
            counts[f"{section}_seen"] += 1
            name = str(entry.get("name") or "").strip()
            funder = str(entry.get("funder") or "").strip()
            if not name or name == "?":
                # No title — the record cannot be identified or
                # displayed. Skipped, counted, never guessed.
                counts["skipped_incomplete"] += 1
                continue
            source_key = grant_source_key(name, funder)
            if source_key in seen_keys:
                # The same grant twice in one file (the three
                # known overlaps): the first entry wins.
                counts["skipped_duplicate"] += 1
                continue
            seen_keys.add(source_key)
            created_anything = False
            if _is_past_deadline(entry):
                counts["past_deadline"] += 1
            confidence = _confidence(entry)
            eligible = _opportunity_eligible(entry)
            if not eligible:
                counts["skipped_not_nonprofit_eligible"] += 1

            asset = await find_asset_by_source(db, entity.id, source_key)
            description = _entry_description(entry, for_opportunity=False)
            if asset is None:
                asset = await create_knowledge_asset_endpoint(
                    KnowledgeAssetCreate(
                        entity_id=entity.id,
                        title=_truncate(name),
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
                # The entry's facts changed (e.g. a deadline
                # moved): update through the existing path
                # (version increments). The opportunity is left
                # alone.
                await update_knowledge_asset_endpoint(
                    asset.id,
                    KnowledgeAssetUpdate(description=description),
                    current_user=user,
                    db=db,
                )
                counts["assets_updated"] += 1
                created_anything = True

            if eligible:
                opportunity = await find_opportunity_for_asset(
                    db, entity.id, asset.id
                )
                if opportunity is None:
                    status = (
                        OpportunityStatus.ACCEPTED
                        if (entry.get("tier") or "") == "SUBMITTED"
                        else OpportunityStatus.DETECTED
                    )
                    await create_opportunity(
                        db,
                        user,
                        OpportunityCreate(
                            entity_id=entity.id,
                            title=_truncate(f"Grant opportunity: {name}"),
                            description=_entry_description(
                                entry, for_opportunity=True
                            ),
                            opportunity_type=OPPORTUNITY_TYPE,
                            confidence=confidence,
                            status=status,
                            detector=DETECTOR,
                            source_type="KnowledgeAsset",
                            source_id=asset.id,
                        ),
                    )
                    counts["opportunities_created"] += 1
                    if status is OpportunityStatus.ACCEPTED:
                        counts["opportunities_accepted"] += 1
                    created_anything = True
            if not created_anything:
                counts["skipped_duplicate"] += 1
            # Phase C.1 — Entity Resolution: the funder becomes
            # a real org node (shared, type business) and the
            # asset gains a FUNDED_BY edge to it. A non-OWNS
            # edge also closes the D4 "no relationships" gap
            # for this asset. The hook runs for every entry —
            # eligible or not, new asset or existing — so a
            # re-run still links assets the first run missed.
            if funder and funder != "?":
                url = str(entry.get("url") or "").strip()
                host = ""
                if url and url != "unverified":
                    try:
                        host = (urlparse(url).hostname or "").lower()
                    except ValueError:
                        host = ""
                resolution = await resolve_or_create(
                    db,
                    name=funder,
                    entity_type=EntityType.BUSINESS.value,
                    context={"domain": host} if host else {},
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
                    relationship_type=GraphRelationshipType.FUNDED_BY.value,
                    confidence=resolution.confidence,
                ):
                    counts["funded_by_edges_created"] += 1
            await db.commit()

    # Entries that disappear from the file are not auto-dismissed
    # — the import never deletes or closes user records — but
    # they are counted so the operator can see them.
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
