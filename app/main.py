"""Freedom GP$ API — application entrypoint.

FastAPI app titled "Freedom GP$ API", v1.0, "Financial Intelligence
Operating System". Mounts the score routers (public, pure computation)
and the v1 resource routers (users, assets, liabilities, reports,
omnisight, scenarios, shadow ledger).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    assets,
    entities,
    liabilities,
    omnisight,
    reports,
    scenarios,
    scores,
    shadow_ledger,
    state_vector,
    users,
)
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
    openapi_version="3.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public scoring endpoints (pure computation, no auth required).
app.include_router(scores.router)

# v1 resource endpoints.
app.include_router(users.router)
app.include_router(assets.router)
app.include_router(liabilities.router)
app.include_router(reports.router)
app.include_router(omnisight.router)
app.include_router(scenarios.router)
app.include_router(shadow_ledger.router)
app.include_router(state_vector.router)
app.include_router(entities.router)


@app.get("/", tags=["meta"])
async def root() -> dict:
    """Service identity."""
    return {"system": "Freedom GP$", "version": "1.0"}


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}
