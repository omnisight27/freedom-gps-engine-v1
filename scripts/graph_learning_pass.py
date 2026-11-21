#!/usr/bin/env python3
"""CLI: run the Opportunity Engine's detectors over the populated
graph — EO 007A Phase C part 2, the graph learning pass.

This is where the graph begins learning. Phases A and B loaded
real imported data into the engine (NexIntel predictions,
SAM.gov contract opportunities); part 1 of Phase C adds the
grants. The graph now holds real records, and this pass runs the
EXISTING detectors over them through the existing detect path —
the same run_detection() the POST /api/v1/opportunities/detect
endpoint calls. No new detectors, scores, models, or engines.

What "the graph begins learning" measurably means for this
phase:

- The detectors execute over real imported data, not fixtures.
  D4 (knowledge assets with no relationships) fires on the
  imported assets: every imported grant and contract asset has
  only its OWNS edge, so the graph itself surfaces that the
  imported knowledge is unconnected — the first lesson the
  graph teaches.
- D6 (disconnected clusters) reports on whether any part of
  the graph is detached from the ledger (expected: none — every
  imported asset is owned by the entity).
- D1/D2/D3/D5 report zero on imported data (no skills,
  certifications, or legacy assets in the connector feeds) —
  the zeros are reported, not hidden, because a detector that
  finds nothing is also evidence.
- The reconcile guard holds: a detection run never closes an
  opportunity created by an importer (a foreign detector value
  such as "contract_intel_sam_gov" or "grants_curated_nicnacs").
  Only opportunities the six detectors themselves created are
  ever auto-dismissed. Without that guard, the first learning
  pass would have destroyed every Phase B opportunity.

Usage (against the deployed engine, once it exists):

    DATABASE_URL=postgresql+asyncpg://... \\
    python scripts/graph_learning_pass.py \\
        --user-email nic@example.com --entity-id 1

Or with an explicit database URL:

    python scripts/graph_learning_pass.py \\
        --database-url sqlite+aiosqlite:///tmp/check.db \\
        --user-email nic@example.com --entity-id 1

The target entity must already exist and be owned by the user.

--dry-run computes the detectors' findings without writing
anything: it calls the six detector functions directly and
reports what a real run would create.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# Allow running from the repo root: `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401  (register models on Base.metadata)
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.graph import build_graph_service  # noqa: E402
from app.models.entity import Entity  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.opportunity_service import (  # noqa: E402
    _detect_certs_evidence_no_utilization,
    _detect_disconnected_clusters,
    _detect_knowledge_no_relationships,
    _detect_legacy_no_preservation,
    _detect_skills_evidence_no_utilization,
    _detect_skills_no_evidence,
    run_detection,
)


def _opp_summary(opp: Opportunity) -> dict:
    return {
        "id": opp.id,
        "title": opp.title,
        "detector": opp.detector,
        "opportunity_type": opp.opportunity_type,
        "status": opp.status,
        "source_type": opp.source_type,
        "source_id": opp.source_id,
    }


async def _open_by_detector(session: AsyncSession, entity_id: int) -> dict:
    rows = (
        await session.execute(
            select(Opportunity).where(Opportunity.entity_id == entity_id)
        )
    ).scalars().all()
    counts: Counter = Counter()
    for opp in rows:
        counts[f"{opp.detector}:{opp.status}"] += 1
    return dict(sorted(counts.items()))


async def _graph_stats(graph, user: User) -> dict:
    """Full node/edge census for the caller's graph, paged (the
    service methods take limit/offset; a single page would
    truncate a populated graph — the Phase C population is
    1,609 assets on one entity)."""
    nodes = []
    offset = 0
    while True:
        page = await graph.list_accessible_nodes(
            user, limit=1000, offset=offset
        )
        nodes.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    edges = []
    offset = 0
    while True:
        page = await graph.list_accessible_edges(
            user, limit=1000, offset=offset
        )
        edges.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return {
        "nodes": len(nodes),
        "nodes_by_type": dict(
            sorted(Counter(n.node_type for n in nodes).items())
        ),
        "edges": len(edges),
        "edges_by_type": dict(
            sorted(Counter(e.relationship_type for e in edges).items())
        ),
    }


async def _dry_run(session: AsyncSession, graph, user: User, entity: Entity) -> dict:
    """Compute the detectors' findings without writing. The six
    detector functions are read-only; run_detection (which
    creates and dismisses) is never called here."""
    findings_by_detector: dict[str, list[dict]] = {}
    for label, coro in (
        (
            "skills_no_evidence",
            _detect_skills_no_evidence(graph, user, entity),
        ),
        (
            "skills_evidence_no_utilization",
            _detect_skills_evidence_no_utilization(
                session, graph, user, entity
            ),
        ),
        (
            "certifications_evidence_no_utilization",
            _detect_certs_evidence_no_utilization(
                session, graph, user, entity
            ),
        ),
        (
            "knowledge_assets_no_relationships",
            _detect_knowledge_no_relationships(
                session, graph, user, entity
            ),
        ),
        (
            "legacy_no_preservation_status",
            _detect_legacy_no_preservation(graph, user, entity),
        ),
        (
            "disconnected_clusters",
            _detect_disconnected_clusters(graph, user, entity),
        ),
    ):
        findings = await coro
        findings_by_detector[label] = [
            {
                "title": f["title"],
                "detector": f["detector"],
                "source_type": f["source_type"],
                "source_id": f["source_id"],
            }
            for f in findings
        ]
    return {
        "entity_id": entity.id,
        "dry_run": True,
        "graph": await _graph_stats(graph, user),
        "open_by_detector": await _open_by_detector(session, entity.id),
        "findings_by_detector": findings_by_detector,
        "findings_total": sum(
            len(v) for v in findings_by_detector.values()
        ),
    }


async def _run(args: argparse.Namespace) -> dict:
    engine = create_async_engine(
        args.database_url, echo=False, future=True
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(User).where(User.email == args.user_email)
            )
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(
                f"No user with email {args.user_email!r} — "
                "create the user first (POST /api/v1/users)."
            )
        entity = (
            await session.execute(
                select(Entity).where(Entity.id == args.entity_id)
            )
        ).scalar_one_or_none()
        if entity is None:
            raise SystemExit(
                f"No entity with id {args.entity_id} — create it "
                "first (POST /api/v1/entities)."
            )
        graph = build_graph_service(session)
        if args.dry_run:
            return await _dry_run(session, graph, user, entity)
        open_before = await _open_by_detector(session, entity.id)
        result = await run_detection(session, graph, user, entity)
        await session.commit()
        for opp in result["created"] + result["dismissed"]:
            await session.refresh(opp)
        return {
            "entity_id": entity.id,
            "dry_run": False,
            "graph": await _graph_stats(graph, user),
            "open_before": open_before,
            "created": [_opp_summary(o) for o in result["created"]],
            "created_by_detector": dict(
                sorted(
                    Counter(
                        o.detector for o in result["created"]
                    ).items()
                )
            ),
            "dismissed": [_opp_summary(o) for o in result["dismissed"]],
            "dismissed_by_detector": dict(
                sorted(
                    Counter(
                        o.detector for o in result["dismissed"]
                    ).items()
                )
            ),
            "open_after": await _open_by_detector(session, entity.id),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Opportunity Engine's detectors over the "
            "populated graph (EO 007A Phase C, graph learning pass)."
        )
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL (default: settings.database_url)",
    )
    parser.add_argument(
        "--user-email",
        required=True,
        help="Email of the user who owns the target entity",
    )
    parser.add_argument(
        "--entity-id",
        required=True,
        type=int,
        help="Id of the entity to run detection for",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the detectors' findings without writing",
    )
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
