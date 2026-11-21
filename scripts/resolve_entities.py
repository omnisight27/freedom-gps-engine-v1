#!/usr/bin/env python3
"""CLI: run the Phase C.1 entity-resolution pass over the
Freedom GP$ engine.

Scans every entity grouped by normalized key, merges the
unambiguous duplicates (same type + same normalized canonical
key) into the earliest-created member, queues reviews for the
ambiguous cases (alias-key overlaps, domain-shared orgs), scans
assets by normalized (funder/agency + title) for near-duplicates
the slug key missed (reported only — assets are never merged by
this pass), and prints before/after metrics with the
Architect's measures (Entity Resolution Rate, Duplicate
Compression Ratio, Duplicate Reduction %, relationship density
and findings on replay).

Usage (against the deployed engine, once it exists):

    python scripts/resolve_entities.py \\
        --user-email nic@example.com

Or with an explicit database URL:

    python scripts/resolve_entities.py \\
        --database-url sqlite+aiosqlite:////tmp/engine.db \\
        --user-email nic@example.com

The user must already exist (POST /api/v1/users); the pass is
global, not per-entity, so no --entity-id is needed. The user
is the audit actor for every merge and review the pass
creates.

--dry-run reports what would merge and queue without writing
anything. Re-running the pass is safe: merges are idempotent
(a second run finds no duplicate groups), and a rejected
(proposed, matched) pair is never re-queued.
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
from app.models.user import User  # noqa: E402
from app.services.entity_resolution import resolution_pass  # noqa: E402


async def _run(args: argparse.Namespace) -> dict:
    engine = create_async_engine(args.database_url, echo=False)
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
        result = await resolution_pass(
            session, user=user, dry_run=args.dry_run
        )
        if not args.dry_run:
            await session.commit()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Phase C.1 entity-resolution pass over the "
            "Freedom GP$ engine (EO 007A Phase C.1)."
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
        help="Email of the user who acts as the audit actor",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would merge and queue without writing",
    )
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
