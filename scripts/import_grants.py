#!/usr/bin/env python3
"""CLI: import the curated NicNacs grants list into the
Freedom GP$ engine.

Reads the OmniSight site's public grants-data.js (a URL or a
local file) and loads it through the engine's existing write
paths (see app/services/grants_import.py). Read-only on the live
data: the file is a curated snapshot (research pass
2026-10-30), not a live feed — this consumes the file, never any
grants API.

Usage (against the deployed engine, once it exists):

    DATABASE_URL=postgresql+asyncpg://... \\
    python scripts/import_grants.py \\
        --user-email nic@example.com --entity-id 1

Or with an explicit database URL and a local file:

    python scripts/import_grants.py \\
        --database-url sqlite+aiosqlite:///tmp/check.db \\
        --grants-source /tmp/grants-data.js \\
        --user-email nic@example.com --entity-id 1

The target entity must already exist and be owned by the user
(create it first, e.g. POST /api/v1/entities — a nonprofit
entity named "NicNacs" is the natural owner of this list, since
the file is the NicNacs grants page; the operator chooses which
entity the opportunities belong to).

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
from app.services.grants_import import (  # noqa: E402
    FEED_URL,
    _is_past_deadline,
    grant_source_key,
    import_grants,
    load_feed,
)


def _dry_run_summary(feed: dict) -> dict:
    """What a real run would see, without touching the database."""
    incomplete = 0
    keys: set[str] = set()
    duplicates = 0
    eligible = 0
    not_eligible = 0
    accepted = 0
    past_deadline = 0
    for section in ("nicnacs", "catalog"):
        for entry in feed.get(section) or []:
            if not isinstance(entry, dict):
                incomplete += 1
                continue
            name = str(entry.get("name") or "").strip()
            funder = str(entry.get("funder") or "").strip()
            if not name or name == "?":
                incomplete += 1
                continue
            key = grant_source_key(name, funder)
            if key in keys:
                duplicates += 1
                continue
            keys.add(key)
            if _is_past_deadline(entry):
                past_deadline += 1
            if entry.get("tier") is not None:
                eligible += 1
                if entry.get("tier") == "SUBMITTED":
                    accepted += 1
            elif entry.get("nonprofit_eligible") is True:
                eligible += 1
            else:
                not_eligible += 1
    return {
        "entries_seen": sum(len(feed.get(s) or []) for s in ("nicnacs", "catalog")),
        "nicnacs_seen": len(feed.get("nicnacs") or []),
        "catalog_seen": len(feed.get("catalog") or []),
        "skipped_incomplete": incomplete,
        "skipped_duplicate": duplicates,
        "skipped_not_nonprofit_eligible": not_eligible,
        "opportunities_would_create": eligible,
        "opportunities_would_accept": accepted,
        "past_deadline": past_deadline,
        "unique_grants": len(keys),
    }


async def _run(args: argparse.Namespace) -> dict:
    feed = load_feed(args.grants_source)
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
        return await import_grants(
            session, user, entity, feed, source_url=args.grants_source
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import the curated NicNacs grants list into the "
            "Freedom GP$ Opportunity Engine (EO 007A Phase C)."
        )
    )
    parser.add_argument(
        "--grants-source",
        default=FEED_URL,
        help="grants-data.js URL or local path (default: the live file)",
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
