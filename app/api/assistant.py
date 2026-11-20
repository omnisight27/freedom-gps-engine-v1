"""OmniSight Assistant endpoints — Engine v1 Sprint 6.

POST   /api/v1/assistant/ask            ask OmniSight a question

The assistant is DETERMINISTIC and EXPLAINABLE: rule-based
intent matching to one of eight supported questions — no LLM, no
AI reasoning, nothing predictive. An unknown question is never
guessed at: it returns a structured unsupported response that
lists the eight supported questions.

All endpoints require auth and are entity-scoped (the Sprint 1
read rule: own entities plus shared ones). Every answer flows
through the assistant service, which reads only through the
existing services — no direct database access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.assistant import AssistantAskRequest, AssistantResponse
from app.services.assistant_service import ask
from app.services.vault_service import resolve_entity_for_read

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


@router.post("/ask", response_model=AssistantResponse)
async def ask_omnisight(
    payload: AssistantAskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AssistantResponse:
    """Ask OmniSight one of the eight supported questions
    (AT-606). Unknown questions return a structured unsupported
    response — they are never guessed at."""
    entity = await resolve_entity_for_read(
        db, payload.entity_id, current_user
    )
    return await ask(db, current_user, entity, payload)
