"""Validation Engine services — Engine v1 Sprint 5.

The science layer: six registries, the seed, drift thresholds,
and the from-prediction calibration path. THE MISSION IS NOT TO
VALIDATE MODELS YET — this is the machinery that allows
validation to occur. Nothing here validates a model; it records
the evidence validation will read.

Seed (SEED_MODELS): the nine named models of Engineering Order
005, all at validation level Experimental — the honest current
state per the Calibration & Validation Framework v1.0, Appendix
A ("No Freedom GP$ score has yet reached Level 3 (Validated) or
Level 4 (Institutional), because no real-world validation data
exists for them"). Five of the nine (Hidden Asset Multiplier,
Stinnes Score, Asset Flipper Score, Anti-Fragility Index,
Regulatory Friction Score) are listed in Appendix A at Level 2
Experimental with the reason "Formula and bands defined;
computed by the v1 reference implementation on illustrative
inputs. No real-world validation data exists." The other four
(GP$ Index, Prosperity Score, Trust Score, Opportunity Score)
are composite Canon scores not listed in Appendix A; they take
the same honest Experimental default, per the Framework's
statement that no score has reached Validated. Formula records:
where an Engine v1 reference implementation exists, the Score
Registry entry quotes its formula; where none exists yet (Trust
Score, Opportunity Score), the entry says so plainly — the
Score Registry is now the source of truth, and the formula
record is completed here first, not invented elsewhere.

Drift thresholds (drift_status): relative change
|current - baseline| / |baseline| — normal at <= 5%, warning
at > 5% and <= 20%, critical above 20%. A zero baseline has no
relative change to measure: any nonzero current is critical, a
zero current is normal. Foundational only — no advanced drift
analytics in Sprint 5.

One outcome per prediction: a second outcome for the same
prediction is rejected with 409 Conflict, never silently
overwritten — the first recorded result is the evidence, and
replacing it would rewrite history.

Calibration error is computed server-side (CE = Observed -
Predicted, Framework v1.0 section 5.3), never client-supplied.
The from-prediction path (AT-503) reads the numeric values from
the prediction's predicted_outcome and the outcome's
actual_result: a top-level JSON number, or an object with a
numeric "value" key. Anything else is a 422 — the machinery
refuses to guess what the number is.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registry import (
    CalibrationRecord,
    DriftRecord,
    DriftStatus,
    ModelVersionHistory,
    Outcome,
    Prediction,
    RegistryModel,
    ScoreRegistry,
)
from app.models.user import User
from app.schemas.registry import (
    CalibrationCreate,
    CalibrationFromPredictionRequest,
    DriftCreate,
    ModelCreate,
    ModelUpdate,
    OutcomeCreate,
    OutcomeUpdate,
    PredictionCreate,
    PredictionUpdate,
    ScoreCreate,
    ScoreUpdate,
)
from app.services.entity_service import (
    assert_entity_access,
    assert_entity_owner,
    get_entity_or_404,
)
from app.services.vault_service import (
    accessible_entity_ids,
    get_record_or_404,
    log_event,
)

# --- Audit event types (AT-505) ---
EVENT_MODEL_REGISTERED = "model_registered"
EVENT_MODEL_UPDATED = "model_updated"
EVENT_MODEL_VERSION_CHANGED = "model_version_changed"
EVENT_MODEL_DELETED = "model_deleted"
EVENT_SCORE_REGISTERED = "score_registered"
EVENT_SCORE_UPDATED = "score_updated"
EVENT_SCORE_DELETED = "score_deleted"
EVENT_PREDICTION_CREATED = "prediction_created"
EVENT_PREDICTION_UPDATED = "prediction_updated"
EVENT_PREDICTION_DELETED = "prediction_deleted"
EVENT_OUTCOME_RECORDED = "outcome_recorded"
EVENT_OUTCOME_UPDATED = "outcome_updated"
EVENT_OUTCOME_DELETED = "outcome_deleted"
EVENT_CALIBRATION_RECORDED = "calibration_recorded"
EVENT_CALIBRATION_DELETED = "calibration_deleted"
EVENT_DRIFT_RECORDED = "drift_recorded"
EVENT_DRIFT_DELETED = "drift_deleted"

# --- Seed: the nine named models (Engineering Order 005) ---
# Each entry carries the Model Registry row and the matching
# Score Registry row (linked by name). All Experimental, all
# active. Formula records quote the Engine v1 reference
# implementation where one exists; where none exists yet, the
# entry says so instead of inventing a formula.
SEED_MODELS: list[dict] = [
    {
        "name": "GP$ Index",
        "version": "1.0",
        "description": (
            "The complete ledger of life — the Master Index over "
            "the state vector. No Freedom GP$ score has reached "
            "Validated: no real-world validation data exists yet."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "Master Index — weighted formula over the state "
            "vector Ω(t); Master Index coefficients uncalibrated "
            "(Mathematical Framework v1.0)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Prosperity Score",
        "version": "1.0",
        "description": (
            "The Unified Prosperity Equation output. No Freedom "
            "GP$ score has reached Validated: no real-world "
            "validation data exists yet."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "P = (F+H+S+R+L) × (T+I+M) / E (Unified Prosperity "
            "Equation, Mathematical Framework v1.0)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Trust Score",
        "version": "1.0",
        "description": (
            "Trust Score, as defined in the Mathematical "
            "Framework v1.0. No Freedom GP$ score has reached "
            "Validated: no real-world validation data exists yet."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "Trust Score (Mathematical Framework v1.0). No Engine "
            "v1 reference implementation yet — this Score "
            "Registry entry is the formula record; update here "
            "first."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Opportunity Score",
        "version": "1.0",
        "description": (
            "Opportunity Score, as defined in the Mathematical "
            "Framework v1.0. No Freedom GP$ score has reached "
            "Validated: no real-world validation data exists yet."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "Opportunity Score (Mathematical Framework v1.0). No "
            "Engine v1 reference implementation yet — this Score "
            "Registry entry is the formula record; update here "
            "first."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Hidden Asset Multiplier",
        "version": "1.0",
        "description": (
            "Formula defined; computed by the v1 reference "
            "implementation on illustrative inputs. Conversion to "
            "observable value not yet demonstrated."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "HAM = (IP + Skills + Licenses + Network + Brand + "
            "Education + Future Royalties) / Reported Net Worth "
            "(Shadow Ledger engine, app/engines/shadow_ledger.py)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Stinnes Score",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "SS = (Cash Flow Assets + Appreciating Assets + "
            "Hidden Assets) / (Debt Principal + Debt Carry Cost "
            "+ Debt Risk) (Stinnes engine, "
            "app/engines/stinnes.py)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Asset Flipper Score",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "AFS = (Productive Assets + Hidden Assets) / Total "
            "Assets × 100 (Asset Flipper engine, "
            "app/engines/asset_flipper.py)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Anti-Fragility Index",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "AFI = Stress Scenario Net Worth / Current Net Worth "
            "(Anti-Fragility engine, "
            "app/engines/anti_fragility.py)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
    {
        "name": "Regulatory Friction Score",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "validation_level": "Experimental",
        "status": "active",
        "owner": None,
        "formula": (
            "RFS = Ownership Restrictions + Tax Friction + "
            "Capital Controls + Liquidity Restrictions + "
            "Compliance Burden (Regulatory Friction engine, "
            "app/engines/regulatory_friction.py)."
        ),
        "current_status": "active",
        "active_version": "1.0",
    },
]

# Drift thresholds (relative change): normal <= 5%, warning
# > 5% and <= 20%, critical above 20%.
DRIFT_NORMAL_MAX = 0.05
DRIFT_WARNING_MAX = 0.20


def drift_status(baseline: float, current: float) -> str:
    """Derive a drift status from baseline and current.

    Relative change |current - baseline| / |baseline|. A zero
    baseline has no relative change to measure: any nonzero
    current is a full-scale move (critical), a zero current is
    no move (normal).
    """
    if baseline == 0:
        return (
            DriftStatus.CRITICAL.value
            if current != 0
            else DriftStatus.NORMAL.value
        )
    relative = abs(current - baseline) / abs(baseline)
    if relative <= DRIFT_NORMAL_MAX:
        return DriftStatus.NORMAL.value
    if relative <= DRIFT_WARNING_MAX:
        return DriftStatus.WARNING.value
    return DriftStatus.CRITICAL.value


async def seed_registry(db: AsyncSession) -> dict:
    """Seed the Model Registry and Score Registry with the nine
    named models. Idempotent: rows already present (by name) are
    left alone. Returns {"models_created": n, "scores_created":
    n}. The migration 0007 runs the same seed as SQL; this
    function is the in-code source of the seed list and the one
    tests exercise."""
    models_created = 0
    scores_created = 0
    for entry in SEED_MODELS:
        existing = await db.execute(
            select(RegistryModel).where(
                RegistryModel.name == entry["name"]
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(
                RegistryModel(
                    name=entry["name"],
                    version=entry["version"],
                    description=entry["description"],
                    validation_level=entry["validation_level"],
                    status=entry["status"],
                    owner=entry["owner"],
                )
            )
            models_created += 1
        existing_score = await db.execute(
            select(ScoreRegistry).where(
                ScoreRegistry.score_name == entry["name"]
            )
        )
        if existing_score.scalar_one_or_none() is None:
            db.add(
                ScoreRegistry(
                    score_name=entry["name"],
                    current_formula=entry["formula"],
                    current_status=entry["current_status"],
                    validation_level=entry["validation_level"],
                    active_version=entry["active_version"],
                )
            )
            scores_created += 1
    await db.flush()
    return {
        "models_created": models_created,
        "scores_created": scores_created,
    }


# --- Loaders ---
# NOTE: vault_service.get_record_or_404 assumes an `id` primary
# key. Registry tables use the order's key names (model_id,
# prediction_id, outcome_id, record_id), so the loaders below
# select on the real PK column.
async def _get_or_404(db: AsyncSession, model, pk, record_id: int):
    result = await db.execute(select(model).where(pk == record_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{model.__name__} not found",
        )
    return row


async def get_model_or_404(
    db: AsyncSession, model_id: int
) -> RegistryModel:
    return await _get_or_404(
        db, RegistryModel, RegistryModel.model_id, model_id
    )


async def get_score_or_404(
    db: AsyncSession, score_name: str
) -> ScoreRegistry:
    result = await db.execute(
        select(ScoreRegistry).where(
            ScoreRegistry.score_name == score_name
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score not found",
        )
    return row


async def get_prediction_or_404(
    db: AsyncSession, prediction_id: int
) -> Prediction:
    return await _get_or_404(
        db, Prediction, Prediction.prediction_id, prediction_id
    )


async def get_outcome_or_404(
    db: AsyncSession, outcome_id: int
) -> Outcome:
    return await _get_or_404(
        db, Outcome, Outcome.outcome_id, outcome_id
    )


async def get_calibration_or_404(
    db: AsyncSession, record_id: int
) -> CalibrationRecord:
    return await _get_or_404(
        db, CalibrationRecord, CalibrationRecord.record_id, record_id
    )


async def get_drift_or_404(
    db: AsyncSession, record_id: int
) -> DriftRecord:
    return await get_record_or_404(db, DriftRecord, record_id)


async def _check_model_name_free(
    db: AsyncSession, name: str, *, exclude_id: int | None = None
) -> None:
    """Raise 409 when another model already holds this name."""
    stmt = select(RegistryModel).where(RegistryModel.name == name)
    if exclude_id is not None:
        stmt = stmt.where(RegistryModel.model_id != exclude_id)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A model named '{name}' already exists",
        )


def _apply_patch(row, data: dict) -> None:
    """Apply a PATCH payload: only supplied, non-None values
    change (None means "no change", matching the repo's PATCH
    semantics)."""
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value)


def _enum_values(data: dict, *fields: str) -> dict:
    """Convert enum members in a PATCH dict to plain strings."""
    for field in fields:
        if data.get(field) is not None:
            data[field] = data[field].value
    return data


# --- Model Registry ---
async def create_model(
    db: AsyncSession, user: User, payload: ModelCreate
) -> RegistryModel:
    """Register a model (AT-501). Names are unique — a duplicate
    is a 409, not a second row."""
    await _check_model_name_free(db, payload.name)
    model = RegistryModel(
        name=payload.name,
        version=payload.version,
        description=payload.description,
        validation_level=payload.validation_level.value,
        status=payload.status.value,
        owner=payload.owner,
    )
    db.add(model)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_MODEL_REGISTERED,
        entity_id=None,
        record_type="RegistryModel",
        record_id=model.model_id,
        actor=user.email,
        details={
            "name": model.name,
            "version": model.version,
            "validation_level": model.validation_level,
        },
    )
    return model


async def update_model(
    db: AsyncSession,
    user: User,
    model: RegistryModel,
    payload: ModelUpdate,
) -> RegistryModel:
    """Update a model (AT-504). A version change preserves the
    previous version in the version history (AT-506) before the
    row changes, and writes a model_version_changed audit entry
    in addition to model_updated."""
    data = _enum_values(
        payload.model_dump(), "validation_level", "status"
    )
    if data.get("name") is not None:
        await _check_model_name_free(
            db, data["name"], exclude_id=model.model_id
        )
    old_version = model.version
    old_description = model.description
    old_level = model.validation_level
    version_changed = (
        data.get("version") is not None
        and data["version"] != old_version
    )
    _apply_patch(model, data)
    if version_changed:
        db.add(
            ModelVersionHistory(
                model_id=model.model_id,
                version=old_version,
                description=old_description,
                validation_level=old_level,
            )
        )
        log_event(
            db,
            event_type=EVENT_MODEL_VERSION_CHANGED,
            entity_id=None,
            record_type="RegistryModel",
            record_id=model.model_id,
            actor=user.email,
            details={
                "from_version": old_version,
                "to_version": model.version,
            },
        )
    log_event(
        db,
        event_type=EVENT_MODEL_UPDATED,
        entity_id=None,
        record_type="RegistryModel",
        record_id=model.model_id,
        actor=user.email,
        details={
            "version": model.version,
            "validation_level": model.validation_level,
            "status": model.status,
        },
    )
    return model


async def delete_model(
    db: AsyncSession, user: User, model: RegistryModel
) -> None:
    """Delete a model. Its version history, predictions,
    calibration records, and drift records cascade — they are
    evidence about that model, and evidence about a deleted
    model does not outlive it."""
    log_event(
        db,
        event_type=EVENT_MODEL_DELETED,
        entity_id=None,
        record_type="RegistryModel",
        record_id=model.model_id,
        actor=user.email,
        details={"name": model.name, "version": model.version},
    )
    await db.delete(model)


async def list_models(
    db: AsyncSession,
    *,
    validation_level: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[RegistryModel]:
    stmt = select(RegistryModel)
    if validation_level is not None:
        stmt = stmt.where(
            RegistryModel.validation_level == validation_level
        )
    if status_filter is not None:
        stmt = stmt.where(RegistryModel.status == status_filter)
    stmt = (
        stmt.order_by(RegistryModel.model_id).limit(limit).offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_model_versions(
    db: AsyncSession, model: RegistryModel
) -> list[ModelVersionHistory]:
    """A model's preserved previous versions, newest first
    (AT-506)."""
    result = await db.execute(
        select(ModelVersionHistory)
        .where(ModelVersionHistory.model_id == model.model_id)
        .order_by(ModelVersionHistory.id.desc())
    )
    return list(result.scalars().all())


# --- Score Registry ---
async def create_score(
    db: AsyncSession, user: User, payload: ScoreCreate
) -> ScoreRegistry:
    """Register a score. score_name is the key — a duplicate is
    a 409, not a second row."""
    result = await db.execute(
        select(ScoreRegistry).where(
            ScoreRegistry.score_name == payload.score_name
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A score named '{payload.score_name}' already exists",
        )
    score = ScoreRegistry(
        score_name=payload.score_name,
        current_formula=payload.current_formula,
        current_status=payload.current_status,
        validation_level=payload.validation_level.value,
        active_version=payload.active_version,
    )
    db.add(score)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_SCORE_REGISTERED,
        entity_id=None,
        record_type="RegistryScore",
        record_id=None,
        actor=user.email,
        details={
            "score_name": score.score_name,
            "active_version": score.active_version,
            "validation_level": score.validation_level,
        },
    )
    return score


async def update_score(
    db: AsyncSession,
    user: User,
    score: ScoreRegistry,
    payload: ScoreUpdate,
) -> ScoreRegistry:
    """Update a score. score_name is the key and cannot be
    renamed — delete and re-create instead."""
    data = _enum_values(payload.model_dump(), "validation_level")
    _apply_patch(score, data)
    log_event(
        db,
        event_type=EVENT_SCORE_UPDATED,
        entity_id=None,
        record_type="RegistryScore",
        record_id=None,
        actor=user.email,
        details={
            "score_name": score.score_name,
            "active_version": score.active_version,
            "validation_level": score.validation_level,
        },
    )
    return score


async def delete_score(
    db: AsyncSession, user: User, score: ScoreRegistry
) -> None:
    log_event(
        db,
        event_type=EVENT_SCORE_DELETED,
        entity_id=None,
        record_type="RegistryScore",
        record_id=None,
        actor=user.email,
        details={"score_name": score.score_name},
    )
    await db.delete(score)


async def list_scores(
    db: AsyncSession,
    *,
    validation_level: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ScoreRegistry]:
    stmt = select(ScoreRegistry)
    if validation_level is not None:
        stmt = stmt.where(
            ScoreRegistry.validation_level == validation_level
        )
    stmt = (
        stmt.order_by(ScoreRegistry.score_name)
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


# --- Prediction Registry ---
async def create_prediction(
    db: AsyncSession, user: User, payload: PredictionCreate
) -> Prediction:
    """Store a hypothesis (AT-502). The entity must be owned by
    the caller; the model must exist. Nothing is validated yet —
    this is collecting evidence."""
    entity = await get_entity_or_404(db, payload.entity_id)
    assert_entity_owner(entity, user)
    model = await get_model_or_404(db, payload.model_id)
    pred = Prediction(
        entity_id=entity.id,
        model_id=model.model_id,
        forecast_horizon=payload.forecast_horizon,
        predicted_outcome=payload.predicted_outcome,
        confidence=payload.confidence,
    )
    db.add(pred)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_PREDICTION_CREATED,
        entity_id=pred.entity_id,
        record_type="Prediction",
        record_id=pred.prediction_id,
        actor=user.email,
        details={
            "model_id": pred.model_id,
            "forecast_horizon": pred.forecast_horizon,
            "confidence": pred.confidence,
        },
    )
    return pred


async def update_prediction(
    db: AsyncSession,
    user: User,
    pred: Prediction,
    payload: PredictionUpdate,
) -> Prediction:
    data = payload.model_dump()
    _apply_patch(pred, data)
    log_event(
        db,
        event_type=EVENT_PREDICTION_UPDATED,
        entity_id=pred.entity_id,
        record_type="Prediction",
        record_id=pred.prediction_id,
        actor=user.email,
        details={"forecast_horizon": pred.forecast_horizon},
    )
    return pred


async def delete_prediction(
    db: AsyncSession, user: User, pred: Prediction
) -> None:
    """Delete a prediction. Its outcomes cascade — an outcome
    without its prediction is not evidence."""
    log_event(
        db,
        event_type=EVENT_PREDICTION_DELETED,
        entity_id=pred.entity_id,
        record_type="Prediction",
        record_id=pred.prediction_id,
        actor=user.email,
        details={"model_id": pred.model_id},
    )
    await db.delete(pred)


async def list_predictions(
    db: AsyncSession,
    user: User,
    *,
    entity_id: int | None = None,
    model_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Prediction]:
    """Predictions on entities the caller may read (their own
    plus shared ones), optionally filtered."""
    ids = await accessible_entity_ids(db, user)
    stmt = select(Prediction).where(Prediction.entity_id.in_(ids))
    if entity_id is not None:
        entity = await get_entity_or_404(db, entity_id)
        assert_entity_access(entity, user)
        stmt = stmt.where(Prediction.entity_id == entity_id)
    if model_id is not None:
        stmt = stmt.where(Prediction.model_id == model_id)
    stmt = (
        stmt.order_by(Prediction.prediction_id)
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


async def assert_prediction_access(
    db: AsyncSession, pred: Prediction, user: User
) -> None:
    """Raise 403 unless the caller may read the prediction's
    entity."""
    entity = await get_entity_or_404(db, pred.entity_id)
    assert_entity_access(entity, user)


async def assert_prediction_owner(
    db: AsyncSession, pred: Prediction, user: User
) -> None:
    """Raise 403 unless the caller owns the prediction's
    entity."""
    entity = await get_entity_or_404(db, pred.entity_id)
    assert_entity_owner(entity, user)


# --- Outcome Registry ---
async def create_outcome(
    db: AsyncSession, user: User, payload: OutcomeCreate
) -> Outcome:
    """Record an actual result (AT-502). One outcome per
    prediction: a second outcome for the same prediction is
    rejected with 409, never silently overwritten."""
    pred = await get_prediction_or_404(db, payload.prediction_id)
    await assert_prediction_owner(db, pred, user)
    existing = await db.execute(
        select(Outcome).where(
            Outcome.prediction_id == pred.prediction_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This prediction already has an outcome recorded; "
                "update the existing outcome instead of recording "
                "a second one"
            ),
        )
    outcome = Outcome(
        prediction_id=pred.prediction_id,
        actual_result=payload.actual_result,
        notes=payload.notes,
    )
    db.add(outcome)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_OUTCOME_RECORDED,
        entity_id=pred.entity_id,
        record_type="Outcome",
        record_id=outcome.outcome_id,
        actor=user.email,
        details={"prediction_id": pred.prediction_id},
    )
    return outcome


async def update_outcome(
    db: AsyncSession,
    user: User,
    outcome: Outcome,
    payload: OutcomeUpdate,
) -> Outcome:
    data = payload.model_dump()
    _apply_patch(outcome, data)
    pred = await get_prediction_or_404(db, outcome.prediction_id)
    log_event(
        db,
        event_type=EVENT_OUTCOME_UPDATED,
        entity_id=pred.entity_id,
        record_type="Outcome",
        record_id=outcome.outcome_id,
        actor=user.email,
        details={"prediction_id": outcome.prediction_id},
    )
    return outcome


async def delete_outcome(
    db: AsyncSession, user: User, outcome: Outcome
) -> None:
    pred = await get_prediction_or_404(db, outcome.prediction_id)
    log_event(
        db,
        event_type=EVENT_OUTCOME_DELETED,
        entity_id=pred.entity_id,
        record_type="Outcome",
        record_id=outcome.outcome_id,
        actor=user.email,
        details={"prediction_id": outcome.prediction_id},
    )
    await db.delete(outcome)


async def list_outcomes(
    db: AsyncSession,
    user: User,
    *,
    prediction_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Outcome]:
    """Outcomes on predictions the caller may read (their own
    entities plus shared ones), optionally filtered."""
    ids = await accessible_entity_ids(db, user)
    stmt = (
        select(Outcome)
        .join(Prediction, Outcome.prediction_id == Prediction.prediction_id)
        .where(Prediction.entity_id.in_(ids))
    )
    if prediction_id is not None:
        pred = await get_prediction_or_404(db, prediction_id)
        await assert_prediction_access(db, pred, user)
        stmt = stmt.where(Outcome.prediction_id == prediction_id)
    stmt = (
        stmt.order_by(Outcome.outcome_id).limit(limit).offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


async def assert_outcome_access(
    db: AsyncSession, outcome: Outcome, user: User
) -> None:
    pred = await get_prediction_or_404(db, outcome.prediction_id)
    await assert_prediction_access(db, pred, user)


async def assert_outcome_owner(
    db: AsyncSession, outcome: Outcome, user: User
) -> None:
    pred = await get_prediction_or_404(db, outcome.prediction_id)
    await assert_prediction_owner(db, pred, user)


# --- Calibration Registry ---
def _numeric_value(value) -> float:
    """Read a number out of a prediction/outcome JSON value: a
    top-level number, or an object with a numeric "value" key.
    Anything else is a 422 — the machinery refuses to guess
    what the number is. Booleans are not numbers here."""
    if isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A boolean is not a numeric outcome value",
        )
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "value" in value:
        inner = value["value"]
        if isinstance(inner, bool) or not isinstance(inner, (int, float)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "The outcome's 'value' must be a number to "
                    "calibrate against"
                ),
            )
        return float(inner)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            "The outcome value must be a number, or an object "
            "with a numeric 'value' key, to calibrate against"
        ),
    )


async def create_calibration(
    db: AsyncSession, user: User, payload: CalibrationCreate
) -> CalibrationRecord:
    """Record a calibration measurement (AT-503). CE = Observed
    - Predicted, computed server-side — the schema has no error
    field, so a client-supplied error is ignored."""
    model = await get_model_or_404(db, payload.model_id)
    if payload.prediction_id is not None:
        await get_prediction_or_404(db, payload.prediction_id)
    if payload.outcome_id is not None:
        await get_outcome_or_404(db, payload.outcome_id)
    record = CalibrationRecord(
        model_id=model.model_id,
        observed_value=payload.observed_value,
        predicted_value=payload.predicted_value,
        error=payload.observed_value - payload.predicted_value,
        prediction_id=payload.prediction_id,
        outcome_id=payload.outcome_id,
    )
    db.add(record)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_CALIBRATION_RECORDED,
        entity_id=None,
        record_type="CalibrationRecord",
        record_id=record.record_id,
        actor=user.email,
        details={
            "model_id": record.model_id,
            "observed_value": record.observed_value,
            "predicted_value": record.predicted_value,
            "error": record.error,
        },
    )
    return record


async def create_calibration_from_prediction(
    db: AsyncSession,
    user: User,
    payload: CalibrationFromPredictionRequest,
) -> CalibrationRecord:
    """Generate a calibration record from a prediction + outcome
    pair (AT-503). The outcome must belong to the prediction;
    the model comes from the prediction. CE = Observed -
    Predicted, computed server-side."""
    pred = await get_prediction_or_404(db, payload.prediction_id)
    outcome = await get_outcome_or_404(db, payload.outcome_id)
    if outcome.prediction_id != pred.prediction_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The outcome does not belong to this prediction",
        )
    await assert_prediction_owner(db, pred, user)
    observed = _numeric_value(outcome.actual_result)
    predicted = _numeric_value(pred.predicted_outcome)
    record = CalibrationRecord(
        model_id=pred.model_id,
        observed_value=observed,
        predicted_value=predicted,
        error=observed - predicted,
        prediction_id=pred.prediction_id,
        outcome_id=outcome.outcome_id,
    )
    db.add(record)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_CALIBRATION_RECORDED,
        entity_id=pred.entity_id,
        record_type="CalibrationRecord",
        record_id=record.record_id,
        actor=user.email,
        details={
            "model_id": record.model_id,
            "prediction_id": pred.prediction_id,
            "outcome_id": outcome.outcome_id,
            "observed_value": observed,
            "predicted_value": predicted,
            "error": record.error,
        },
    )
    return record


async def delete_calibration(
    db: AsyncSession, user: User, record: CalibrationRecord
) -> None:
    log_event(
        db,
        event_type=EVENT_CALIBRATION_DELETED,
        entity_id=None,
        record_type="CalibrationRecord",
        record_id=record.record_id,
        actor=user.email,
        details={"model_id": record.model_id},
    )
    await db.delete(record)


async def list_calibration(
    db: AsyncSession,
    *,
    model_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CalibrationRecord]:
    stmt = select(CalibrationRecord)
    if model_id is not None:
        stmt = stmt.where(CalibrationRecord.model_id == model_id)
    stmt = (
        stmt.order_by(CalibrationRecord.record_id)
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


# --- Drift Registry ---
async def create_drift(
    db: AsyncSession, user: User, payload: DriftCreate
) -> DriftRecord:
    """Record drift (AT-507). change = current - baseline and
    status are computed server-side from the drift thresholds."""
    model = await get_model_or_404(db, payload.model_id)
    record = DriftRecord(
        model_id=model.model_id,
        metric=payload.metric,
        baseline=payload.baseline,
        current=payload.current,
        change=payload.current - payload.baseline,
        status=drift_status(payload.baseline, payload.current),
    )
    db.add(record)
    await db.flush()
    log_event(
        db,
        event_type=EVENT_DRIFT_RECORDED,
        entity_id=None,
        record_type="DriftRecord",
        record_id=record.id,
        actor=user.email,
        details={
            "model_id": record.model_id,
            "metric": record.metric,
            "baseline": record.baseline,
            "current": record.current,
            "change": record.change,
            "status": record.status,
        },
    )
    return record


async def delete_drift(
    db: AsyncSession, user: User, record: DriftRecord
) -> None:
    log_event(
        db,
        event_type=EVENT_DRIFT_DELETED,
        entity_id=None,
        record_type="DriftRecord",
        record_id=record.id,
        actor=user.email,
        details={"model_id": record.model_id, "metric": record.metric},
    )
    await db.delete(record)


async def list_drift(
    db: AsyncSession,
    *,
    model_id: int | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DriftRecord]:
    stmt = select(DriftRecord)
    if model_id is not None:
        stmt = stmt.where(DriftRecord.model_id == model_id)
    if status_filter is not None:
        stmt = stmt.where(DriftRecord.status == status_filter)
    stmt = (
        stmt.order_by(DriftRecord.id).limit(limit).offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())
