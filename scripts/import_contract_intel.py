#!/usr/bin/env python3
"""CLI: import the SAM.gov nonprofit contract watch into the
Freedom GP$ engine.

Reads the OmniSight NexIntel loop's public sam_nonprofit.json (a
URL or a local file) and loads it through the engine's existing
write paths (see app/services/contract_import.py). Read-only on
the live data: the loop's keys stay in GitHub Secrets — this
consumes the public output file, never the SAM.gov API.

Usage (against the deployed engine, once it exists):

    DATABASE_URL=postgresql+asyncpg://... \\
    python scripts/import_contract_intel.py \\
        --user-email nic@example.com --entity-id 1

Or with an explicit database URL and a local file:

    python scripts/import_contract_intel.py \\
        --database-url sqlite+aiosqlite:///tmp/check.db \\
        --contract-feed /tmp/sam_nonprofit.json \\
        --user-email nic@example.com --entity-id 1

The target entity must already exist and be owned by the user
(create it first, e.g. POST /api/v1/entities — a nonprofit
entity named "NicNacs" is the natural owner of this feed, since
the loop matched these solicitations for a nonprofit like
NicNacs; the feed is pre-filtered by the loop, and the operator
chooses which entity the opportunities belong to).

--dry-run parses the file and reports what would be imported
without touching the database. Re-running the import is safe:
it never duplicates an asset or an opportunity, updates an
asset whose facts changed, and never recreates a dismissed
opportunity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from the repo root: `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401  (register models on Base.metadata)
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models.entity import Entity  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.contract_import import (  # noqa: E402
    FEED_URL,
    import_contract_intel,
    load_feed,
)


def _dry_run_summary(feed: dict) -> dict:
    """What a real run would see, without touching the database."""
    rows = feed.get("rows") or []
    with_match = without_match = incomplete = 0
    notice_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            incomplete += 1
            continue
        notice_id = str(row.get("notice_id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not notice_id or notice_id == "?" or not title or title == "?":
            incomplete += 1
            continue
        notice_ids.add(notice_id)
        if row.get("nonprofit_match"):
            with_match += 1
        else:
            without_match += 1
    return {
        "rows_seen": len(rows),
        "with_match": with_match,
        "without_match": without_match,
        "skipped_incomplete": incomplete,
        "unique_notice_ids": len(notice_ids),
    }


async def _run(args: argparse.Namespace) -> dict:
    feed = load_feed(args.contract_feed)
    if args.dry_run:
        return _dry_run_summary(feed)

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
        # Create any missing tables (no-op when migrations
        # already created them).
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
        return await import_contract_intel(
            session, user, entity, feed, source_url=args.contract_feed
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import the SAM.gov nonprofit contract watch into the "
            "Freedom GP$ Opportunity Engine (EO 007A Phase B)."
        )
    )
    parser.add_argument(
        "--contract-feed",
        default=FEED_URL,
        help="sam_nonprofit.json URL or local path (default: the live file)",
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
        help="Id of the entity the opportunities belong to",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the file and report counts without writing",
    )
    args = parser.parse_args()
    counts = asyncio.run(_run(args))
    print(json.dumps(counts, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
