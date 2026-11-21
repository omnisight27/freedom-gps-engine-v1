#!/usr/bin/env python3
"""CLI: import the NexIntel pick history into the Freedom GP$ engine.

Reads the OmniSight NexIntel loop's public history.json (a URL or a
local file) and loads it through the engine's existing write paths
(see app/services/nexintel_import.py). Read-only on the live data:
the loop's keys stay in GitHub Secrets — this consumes the public
output file, never the upstream APIs.

Usage (against the deployed engine, once it exists):

    DATABASE_URL=postgresql+asyncpg://... \\
    python scripts/import_nexintel_history.py \\
        --user-email nic@example.com --entity-id 1

Or with an explicit database URL and a local file:

    python scripts/import_nexintel_history.py \\
        --database-url sqlite+aiosqlite:///tmp/check.db \\
        --history /tmp/history.json \\
        --user-email nic@example.com --entity-id 1

The target entity must already exist and be owned by the user
(create it first, e.g. POST /api/v1/entities — an organization
entity named "OmniSight" is the natural owner of the loop's picks,
since they are the platform's output, not one person's ledger).

--dry-run parses the file and reports what would be imported
without touching the database. Re-running the import is safe:
it never duplicates an asset, prediction, outcome, or calibration
record, and it resolves picks that were ungraded on the first run.
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
from app.services.nexintel_import import (  # noqa: E402
    HISTORY_URL,
    import_nexintel_history,
    load_history,
)


def _dry_run_summary(history: dict) -> dict:
    """What a real run would see, without touching the database."""
    runs = history.get("picks") or []
    picks = graded = ungraded = excluded = 0
    for run in runs:
        for side in ("longs", "shorts"):
            for pick in run.get(side) or []:
                picks += 1
                if pick.get("excluded"):
                    excluded += 1
                elif pick.get("win") is not None:
                    graded += 1
                else:
                    ungraded += 1
    stats = history.get("stats") or {}
    return {
        "runs_seen": len(runs),
        "picks_seen": picks,
        "graded_seen": graded,
        "ungraded_seen": ungraded,
        "skipped_excluded": excluded,
        "loop_total_graded": stats.get("total"),
        "loop_win_rate": stats.get("win_rate"),
        "loop_mae": (history.get("error") or {}).get("mae"),
    }


async def _run(args: argparse.Namespace) -> dict:
    history = load_history(args.history)
    if args.dry_run:
        return _dry_run_summary(history)

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
        return await import_nexintel_history(
            session, user, entity, history, source_url=args.history
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import the NexIntel pick history into the Freedom GP$ "
            "Validation Registry (EO 007A Phase A)."
        )
    )
    parser.add_argument(
        "--history",
        default=HISTORY_URL,
        help="history.json URL or local path (default: the live file)",
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
        help="Id of the entity the picks belong to",
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
