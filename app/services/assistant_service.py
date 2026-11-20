"""OmniSight Assistant service — Engine v1 Sprint 6.

The assistant is the capability; the dashboard is the surface.
DETERMINISTIC and EXPLAINABLE: rule-based intent matching
(keyword/phrase patterns) to one of eight supported intents —
NO LLM, NO AI reasoning, NOTHING PREDICTIVE. Each intent maps
to a specific service query; the response carries the intent, a
plain-English answer, the data, the evidence, and the sources
that produced it. An unknown question is never guessed at: it
returns a structured unsupported response that says plainly it
cannot answer that yet and lists the eight supported questions.

Architecture (Engineering Order 006): every interaction flows
through the existing services — no direct database access from
the assistant layer.

Intent notes:
- "Show certifications without evidence" is distinct from
  Sprint 4 Detector 3 (certifications WITH evidence but no
  utilization). This intent queries certifications with no
  evidence at all (vault_service.query_certifications_no_evidence).
- "Why was this opportunity created?" needs an opportunity_id;
  without one it explains the six detectors and lists the
  entity's recent opportunities to choose from.
- "What evidence supports this record?" needs record_id +
  record_type (KnowledgeAsset, Skill, or Certification);
  without them it explains what evidence is and lists the
  entity's recent knowledge assets to choose from.
"""

from __future__ import annotations

import re
import string

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity import Entity
from app.models.user import User
from app.models.vault import (
    Certification,
    KnowledgeAsset,
    LegacyAsset,
    Skill,
)
from app.schemas.assistant import (
    SUPPORTED_QUESTIONS,
    AssistantAskRequest,
    AssistantResponse,
)
from app.schemas.opportunity import OpportunityOut
from app.schemas.registry import ModelOut
from app.schemas.state_vector import StateVectorOut
from app.schemas.vault import (
    EvidenceOut,
    KnowledgeAssetOut,
    LegacyAssetOut,
    SkillOut,
)
from app.services.dashboard_service import COMPONENTS
from app.services.opportunity_service import (
    DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION,
    DETECTOR_DISCONNECTED_CLUSTERS,
    DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS,
    DETECTOR_LEGACY_NO_PRESERVATION,
    DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION,
    DETECTOR_SKILLS_NO_EVIDENCE,
    assert_opportunity_access,
    get_opportunity_or_404,
    list_opportunities,
)
from app.services.registry_service import list_models
from app.services.state_vector_service import (
    get_entity_state_vector_history,
)
from app.services.vault_service import (
    get_record_or_404,
    list_evidence_for_asset,
    list_knowledge_assets,
    query_certifications_no_evidence,
    query_legacy_no_preservation_status,
    query_skills_no_evidence,
)

# Intent names — the `intent` value on the response.
INTENT_OPPORTUNITIES = "show_opportunities"
INTENT_LEDGER_CHANGES = "ledger_changes"
INTENT_LEGACY_NO_PRESERVATION = "legacy_no_preservation"
INTENT_SKILLS_NO_EVIDENCE = "skills_no_evidence"
INTENT_CERTS_NO_EVIDENCE = "certifications_no_evidence"
INTENT_EXPERIMENTAL_MODELS = "experimental_models"
INTENT_OPPORTUNITY_REASON = "opportunity_reason"
INTENT_RECORD_EVIDENCE = "record_evidence"
INTENT_UNSUPPORTED = "unsupported"

# One-line descriptions of the six Sprint 4 detectors, used
# when the user asks why an opportunity was created without
# naming one.
DETECTOR_DESCRIPTIONS: dict[str, str] = {
    DETECTOR_SKILLS_NO_EVIDENCE: (
        "a skill with no supporting evidence"
    ),
    DETECTOR_SKILLS_EVIDENCE_NO_UTILIZATION: (
        "a skill with evidence but no connection to any "
        "opportunity, asset, or value-producing relationship"
    ),
    DETECTOR_CERTS_EVIDENCE_NO_UTILIZATION: (
        "a certification with evidence but no connection to "
        "any opportunity, asset, or value-producing relationship"
    ),
    DETECTOR_KNOWLEDGE_NO_RELATIONSHIPS: (
        "a knowledge asset with no graph relationships beyond "
        "ownership"
    ),
    DETECTOR_LEGACY_NO_PRESERVATION: (
        "a legacy asset with no preservation status"
    ),
    DETECTOR_DISCONNECTED_CLUSTERS: (
        "a cluster of graph nodes disconnected from the ledger"
    ),
}


def _normalize(question: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — so
    matching is deterministic and case-insensitive."""
    lowered = question.lower()
    no_punct = lowered.translate(
        str.maketrans(string.punctuation, " " * len(string.punctuation))
    )
    return re.sub(r"\s+", " ", no_punct).strip()


def _has_word(words: set[str], *forms: str) -> bool:
    """True when any of the given word forms is present — the
    questions use plurals ("skills", "models") and the matcher
    must not miss them for checking only the singular."""
    return any(form in words for form in forms)


def match_intent(question: str) -> str | None:
    """Map a question to one of the eight supported intents, or
    None when it matches none. Order matters: the most specific
    patterns are checked first, so "show skills without
    evidence" never falls through to the evidence intent, and
    "why was this opportunity created" never falls through to
    the generic opportunities intent."""
    q = _normalize(question)
    words = set(q.split())

    # Skills / certifications without evidence — checked before
    # the generic evidence intent (both contain "evidence").
    if _has_word(words, "skill", "skills") and (
        "without evidence" in q or "no evidence" in q
    ):
        return INTENT_SKILLS_NO_EVIDENCE
    if _has_word(words, "certification", "certifications") and (
        "without evidence" in q or "no evidence" in q
    ):
        return INTENT_CERTS_NO_EVIDENCE
    # Legacy assets lacking preservation.
    if "legacy" in words and "preservation" in words:
        return INTENT_LEGACY_NO_PRESERVATION
    # Experimental models.
    if "experimental" in words and _has_word(words, "model", "models"):
        return INTENT_EXPERIMENTAL_MODELS
    # What changed in the ledger.
    if "changed" in words and "ledger" in words:
        return INTENT_LEDGER_CHANGES
    # Why was this opportunity created — checked before the
    # generic opportunities intent.
    if "why" in words and "opportunity" in words:
        return INTENT_OPPORTUNITY_REASON
    # What evidence supports this record.
    if "evidence" in words and (
        "record" in words or "support" in words
    ):
        return INTENT_RECORD_EVIDENCE
    # Show my opportunities.
    if "opportunit" in q:
        return INTENT_OPPORTUNITIES
    return None


def _unsupported_response(question: str) -> AssistantResponse:
    """An unknown question is never guessed at: say plainly it
    cannot be answered yet and list the eight supported
    questions."""
    return AssistantResponse(
        intent=INTENT_UNSUPPORTED,
        answer=(
            "I can't answer that yet. OmniSight answers these "
            "questions today:\n"
            + "\n".join(f"- {q}" for q in SUPPORTED_QUESTIONS)
        ),
        data={"question": question, "supported_questions": SUPPORTED_QUESTIONS},
        evidence=[],
        sources=[],
    )


async def _show_opportunities(
    db: AsyncSession, user: User, entity: Entity
) -> AssistantResponse:
    """Intent 1: the entity's open opportunities."""
    all_opps = await list_opportunities(
        db, user, entity_id=entity.id, limit=200
    )
    open_opps = [
        opp
        for opp in all_opps
        if opp.status in ("detected", "accepted")
    ]
    dismissed = sum(
        1 for opp in all_opps if opp.status == "dismissed"
    )
    resolved = sum(
        1 for opp in all_opps if opp.status == "realized"
    )
    if open_opps:
        answer = (
            f"You have {len(open_opps)} open "
            f"opportunit{'y' if len(open_opps) == 1 else 'ies'}"
        )
    else:
        answer = "You have no open opportunities right now"
    if dismissed or resolved:
        answer += (
            f" ({dismissed} dismissed, {resolved} resolved)."
        )
    else:
        answer += "."
    return AssistantResponse(
        intent=INTENT_OPPORTUNITIES,
        answer=answer,
        data=[OpportunityOut.model_validate(o) for o in open_opps],
        evidence=[],
        sources=["opportunity_service.list_opportunities"],
    )


async def _ledger_changes(
    db: AsyncSession, user: User, entity: Entity
) -> AssistantResponse:
    """Intent 2: latest state vector changes — which components
    moved and by how much."""
    history = await get_entity_state_vector_history(
        db, entity.id, limit=2
    )
    if len(history) < 2:
        current = history[0] if history else None
        return AssistantResponse(
            intent=INTENT_LEDGER_CHANGES,
            answer=(
                "Only one state vector snapshot exists so far, "
                "so there is nothing to compare yet. Record a "
                "second snapshot and I can tell you what moved."
            ),
            data=(
                StateVectorOut.model_validate(current)
                if current is not None
                else None
            ),
            evidence=[],
            sources=[
                "state_vector_service.get_entity_state_vector_history"
            ],
        )
    latest, previous = history[0], history[1]
    moved = []
    for key, letter, name in COMPONENTS:
        current = getattr(latest, key)
        prev = getattr(previous, key)
        if current is None or prev is None or current == prev:
            continue
        moved.append(
            {
                "component": letter,
                "name": name,
                "previous": prev,
                "current": current,
                "delta": current - prev,
            }
        )
    if not moved:
        answer = (
            "Nothing changed in your ledger since the last "
            "snapshot — every measured component is the same."
        )
    else:
        parts = [
            f"{m['name']} moved from {m['previous']} to "
            f"{m['current']} ({m['delta']:+g})"
            for m in moved
        ]
        answer = (
            "Since the last snapshot: " + "; ".join(parts) + "."
        )
    return AssistantResponse(
        intent=INTENT_LEDGER_CHANGES,
        answer=answer,
        data=moved,
        evidence=[],
        sources=[
            "state_vector_service.get_entity_state_vector_history"
        ],
    )


async def _legacy_no_preservation(
    db: AsyncSession, user: User, entity: Entity
) -> AssistantResponse:
    """Intent 3: legacy assets with no preservation status
    (Sprint 4 Detector 5 data)."""
    assets = await query_legacy_no_preservation_status(
        db, user, entity.id
    )
    if assets:
        answer = (
            f"{len(assets)} legacy asset{'s' if len(assets) != 1 else ''} "
            "lack a preservation status: "
            + ", ".join(f"'{a.title}'" for a in assets)
            + ". Set a preservation status so the system can "
            "tell whether this value is protected."
        )
    else:
        answer = (
            "Every legacy asset has a preservation status. "
            "Nothing is unprotected."
        )
    return AssistantResponse(
        intent=INTENT_LEGACY_NO_PRESERVATION,
        answer=answer,
        data=[LegacyAssetOut.model_validate(a) for a in assets],
        evidence=[],
        sources=[
            "vault_service.query_legacy_no_preservation_status"
        ],
    )


async def _skills_no_evidence(
    db: AsyncSession, user: User, entity: Entity
) -> AssistantResponse:
    """Intent 4: skills with no evidence (Sprint 4 Detector 1
    data)."""
    skills = await query_skills_no_evidence(db, user, entity.id)
    if skills:
        answer = (
            f"{len(skills)} skill{'s' if len(skills) != 1 else ''} "
            "have no supporting evidence: "
            + ", ".join(f"'{s.name}'" for s in skills)
            + ". Add evidence — a certificate, a project, a "
            "transcript — to make each capability verifiable."
        )
    else:
        answer = "Every skill has supporting evidence."
    return AssistantResponse(
        intent=INTENT_SKILLS_NO_EVIDENCE,
        answer=answer,
        data=[SkillOut.model_validate(s) for s in skills],
        evidence=[],
        sources=["vault_service.query_skills_no_evidence"],
    )


async def _certs_no_evidence(
    db: AsyncSession, user: User, entity: Entity
) -> AssistantResponse:
    """Intent 5: certifications with no evidence. NOTE: this is
    distinct from Sprint 4 Detector 3, which finds
    certifications WITH evidence but no utilization. This intent
    finds certifications with no evidence at all."""
    certs = await query_certifications_no_evidence(
        db, user, entity.id
    )
    if certs:
        answer = (
            f"{len(certs)} certification{'s' if len(certs) != 1 else ''} "
            "have no supporting evidence: "
            + ", ".join(f"'{c.title}'" for c in certs)
            + ". Attach evidence to make each credential "
            "verifiable."
        )
    else:
        answer = "Every certification has supporting evidence."
    return AssistantResponse(
        intent=INTENT_CERTS_NO_EVIDENCE,
        answer=answer,
        data=[
            {
                "id": c.id,
                "entity_id": c.entity_id,
                "title": c.title,
                "issuer": c.issuer,
            }
            for c in certs
        ],
        evidence=[],
        sources=["vault_service.query_certifications_no_evidence"],
    )


async def _experimental_models(
    db: AsyncSession, user: User, entity: Entity
) -> AssistantResponse:
    """Intent 6: models with validation level Experimental."""
    models = await list_models(
        db, validation_level="Experimental", limit=200
    )
    if models:
        answer = (
            f"{len(models)} model{'s' if len(models) != 1 else ''} "
            "are Experimental: "
            + ", ".join(f"'{m.name}'" for m in models)
            + ". Experimental means the formula and bands are "
            "defined but no real-world validation data exists "
            "yet — nothing here is validated."
        )
    else:
        answer = "No models are at Experimental right now."
    return AssistantResponse(
        intent=INTENT_EXPERIMENTAL_MODELS,
        answer=answer,
        data=[ModelOut.model_validate(m) for m in models],
        evidence=[],
        sources=["registry_service.list_models"],
    )


async def _opportunity_reason(
    db: AsyncSession,
    user: User,
    entity: Entity,
    payload: AssistantAskRequest,
) -> AssistantResponse:
    """Intent 7: why was this opportunity created. With an
    opportunity_id, return that opportunity's detector, source
    record, confidence, and reason. Without one, explain how
    opportunities are created (the six detectors) and list the
    entity's recent opportunities to choose from."""
    if payload.opportunity_id is not None:
        opp = await get_opportunity_or_404(db, payload.opportunity_id)
        await assert_opportunity_access(db, opp, user)
        if opp.entity_id != entity.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Opportunity not found on this entity",
            )
        detector_text = DETECTOR_DESCRIPTIONS.get(
            opp.detector, opp.detector
        )
        source_text = "no linked source record"
        if opp.source_type is not None and opp.source_id is not None:
            source_model = {
                "Skill": Skill,
                "Certification": Certification,
                "KnowledgeAsset": KnowledgeAsset,
                "LegacyAsset": LegacyAsset,
            }.get(opp.source_type)
            if source_model is not None:
                source = await get_record_or_404(
                    db, source_model, opp.source_id
                )
                source_text = (
                    f"{opp.source_type} "
                    f"'{getattr(source, 'title', getattr(source, 'name', ''))}'"
                )
            else:
                source_text = (
                    f"{opp.source_type} #{opp.source_id}"
                )
        answer = (
            f"This opportunity was created by the '{opp.detector}' "
            f"detector, which found {detector_text}. The source "
            f"is {source_text}, and the detector's confidence "
            f"is {opp.confidence}. Reason: {opp.description or '—'}"
        )
        return AssistantResponse(
            intent=INTENT_OPPORTUNITY_REASON,
            answer=answer,
            data={
                "opportunity": OpportunityOut.model_validate(opp),
                "detector": opp.detector,
                "detector_description": detector_text,
                "source": source_text,
                "confidence": opp.confidence,
                "reason": opp.description,
            },
            evidence=[],
            sources=[
                "opportunity_service.get_opportunity_or_404",
                "opportunity_service.list_opportunities",
            ],
        )
    recent = await list_opportunities(
        db, user, entity_id=entity.id, limit=5
    )
    detector_lines = "\n".join(
        f"- {name}: {desc}"
        for name, desc in DETECTOR_DESCRIPTIONS.items()
    )
    answer = (
        "Opportunities are created by six detectors that scan "
        "the graph for gaps:\n"
        + detector_lines
        + "\nTell me which opportunity — here are the recent "
        "ones — and I will explain exactly why it was created."
    )
    return AssistantResponse(
        intent=INTENT_OPPORTUNITY_REASON,
        answer=answer,
        data={
            "detectors": DETECTOR_DESCRIPTIONS,
            "recent_opportunities": [
                OpportunityOut.model_validate(o) for o in recent
            ],
        },
        evidence=[],
        sources=["opportunity_service.list_opportunities"],
    )


async def _record_evidence(
    db: AsyncSession,
    user: User,
    entity: Entity,
    payload: AssistantAskRequest,
) -> AssistantResponse:
    """Intent 8: what evidence supports this record. With
    record_id + record_type (KnowledgeAsset, Skill, or
    Certification), return the evidence linked to that record.
    Without them, explain what evidence is and list the
    entity's recent knowledge assets to choose from."""
    if payload.record_id is not None and payload.record_type is not None:
        record_type = payload.record_type
        asset_id: int | None = None
        record_name = ""
        if record_type == "KnowledgeAsset":
            asset = await get_record_or_404(
                db, KnowledgeAsset, payload.record_id
            )
            if asset.entity_id != entity.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Record not found on this entity",
                )
            asset_id = asset.id
            record_name = f"knowledge asset '{asset.title}'"
        elif record_type in ("Skill", "Certification"):
            model = Skill if record_type == "Skill" else Certification
            row = await get_record_or_404(
                db, model, payload.record_id
            )
            if row.entity_id != entity.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Record not found on this entity",
                )
            record_name = (
                f"{record_type.lower()} "
                f"'{getattr(row, 'name', getattr(row, 'title', ''))}'"
            )
            asset_id = row.knowledge_asset_id
            if asset_id is None:
                return AssistantResponse(
                    intent=INTENT_RECORD_EVIDENCE,
                    answer=(
                        f"The {record_name} has no linked "
                        "knowledge asset, so no evidence is "
                        "attached to it. Link it to a knowledge "
                        "asset and attach evidence there."
                    ),
                    data={
                        "record_type": record_type,
                        "record_id": payload.record_id,
                        "evidence": [],
                    },
                    evidence=[],
                    sources=["vault_service.get_record_or_404"],
                )
        else:
            return AssistantResponse(
                intent=INTENT_RECORD_EVIDENCE,
                answer=(
                    "Evidence attaches to knowledge assets, and "
                    "to skills and certifications through their "
                    "linked knowledge asset. Family records and "
                    "legacy assets carry no evidence records in "
                    "Engine v1 — ask about a knowledge asset, a "
                    "skill, or a certification."
                ),
                data={
                    "record_type": record_type,
                    "record_id": payload.record_id,
                    "evidence": [],
                },
                evidence=[],
                sources=[],
            )
        evidence = await list_evidence_for_asset(
            db, user, asset_id
        )
        if evidence:
            answer = (
                f"The {record_name} is supported by "
                f"{len(evidence)} piece{'s' if len(evidence) != 1 else ''} "
                "of evidence: "
                + ", ".join(
                    f"'{e.source}' ({e.source_type}, "
                    f"confidence {e.confidence})"
                    for e in evidence
                )
                + "."
            )
        else:
            answer = (
                f"The {record_name} has no supporting evidence "
                "yet."
            )
        return AssistantResponse(
            intent=INTENT_RECORD_EVIDENCE,
            answer=answer,
            data={
                "record_type": record_type,
                "record_id": payload.record_id,
                "evidence": [
                    EvidenceOut.model_validate(e) for e in evidence
                ],
            },
            evidence=[
                EvidenceOut.model_validate(e) for e in evidence
            ],
            sources=["vault_service.list_evidence_for_asset"],
        )
    assets = await list_knowledge_assets(
        db, user, entity.id, limit=5
    )
    return AssistantResponse(
        intent=INTENT_RECORD_EVIDENCE,
        answer=(
            "Evidence is what makes a record trustworthy: a "
            "document, transcript, certificate, or other source "
            "attached to a knowledge asset. Tell me which "
            "record — here are the recent knowledge assets — "
            "and I will show you the evidence behind it."
        ),
        data={
            "recent_knowledge_assets": [
                KnowledgeAssetOut.model_validate(a) for a in assets
            ]
        },
        evidence=[],
        sources=["vault_service.list_knowledge_assets"],
    )


async def ask(
    db: AsyncSession,
    user: User,
    entity: Entity,
    payload: AssistantAskRequest,
) -> AssistantResponse:
    """Answer one question. Deterministic: match the intent,
    run the intent's service query, return the structured
    response. Unknown questions are never guessed at."""
    intent = match_intent(payload.question)
    if intent is None:
        return _unsupported_response(payload.question)
    if intent == INTENT_OPPORTUNITIES:
        return await _show_opportunities(db, user, entity)
    if intent == INTENT_LEDGER_CHANGES:
        return await _ledger_changes(db, user, entity)
    if intent == INTENT_LEGACY_NO_PRESERVATION:
        return await _legacy_no_preservation(db, user, entity)
    if intent == INTENT_SKILLS_NO_EVIDENCE:
        return await _skills_no_evidence(db, user, entity)
    if intent == INTENT_CERTS_NO_EVIDENCE:
        return await _certs_no_evidence(db, user, entity)
    if intent == INTENT_EXPERIMENTAL_MODELS:
        return await _experimental_models(db, user, entity)
    if intent == INTENT_OPPORTUNITY_REASON:
        return await _opportunity_reason(db, user, entity, payload)
    if intent == INTENT_RECORD_EVIDENCE:
        return await _record_evidence(db, user, entity, payload)
    return _unsupported_response(payload.question)
