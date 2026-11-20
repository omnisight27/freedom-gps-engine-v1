"""Pydantic v2 request/response models for the OmniSight
Assistant.

Engine v1 Sprint 6 (Engineering Order 006). The assistant is
DETERMINISTIC and EXPLAINABLE: rule-based intent matching to one
of eight supported questions — no LLM, no AI reasoning, nothing
predictive. Each intent maps to a specific service query; the
response carries the intent, a plain-English answer, the data,
the evidence, and the sources that produced it. An unknown
question is never guessed at: it returns a structured
unsupported response that lists the eight supported questions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssistantAskRequest(BaseModel):
    """Ask OmniSight a question about one entity. opportunity_id
    is used by "Why was this opportunity created?"; record_id +
    record_type by "What evidence supports this record?"."""

    question: str = Field(min_length=1, max_length=2000)
    entity_id: int
    opportunity_id: int | None = None
    record_id: int | None = None
    record_type: str | None = Field(default=None, max_length=100)


class AssistantResponse(BaseModel):
    """One answered question. `data` is the structured result;
    `evidence` is the evidence behind it (empty when the answer
    is that evidence is missing); `sources` names the services
    that produced the answer, so every response is traceable."""

    model_config = ConfigDict(from_attributes=True)

    intent: str = Field(
        description="One of the eight supported intents, or 'unsupported'."
    )
    answer: str
    data: Any | None = None
    evidence: list[Any] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


# The eight questions OmniSight answers today. The unsupported
# response lists these verbatim so the user always knows what
# can be asked.
SUPPORTED_QUESTIONS: list[str] = [
    "Show my opportunities.",
    "What changed in my ledger?",
    "What legacy assets lack preservation?",
    "Show skills without evidence.",
    "Show certifications without evidence.",
    "What models are experimental?",
    "Why was this opportunity created?",
    "What evidence supports this record?",
]
