"""Validation Registry endpoints — Engine v1 Sprint 5.

POST   /api/v1/registry/models                  register a model
GET    /api/v1/registry/models                  list (filters:
                                                 validation_level,
                                                 status)
GET    /api/v1/registry/models/{id}             one model
GET    /api/v1/registry/models/{id}/versions    preserved previous
                                                 versions (AT-506)
PATCH  /api/v1/registry/models/{id}             update (a version
                                                 change preserves
                                                 the previous
                                                 version)
DELETE /api/v1/registry/models/{id}             delete a model
POST   /api/v1/registry/scores                  register a score
GET    /api/v1/registry/scores                  list (filter:
                                                 validation_level)
GET    /api/v1/registry/scores/{name}           one score
PATCH  /api/v1/registry/scores/{name}           update a score
DELETE /api/v1/registry/scores/{name}           delete a score
POST   /api/v1/registry/predictions             store a hypothesis
GET    /api/v1/registry/predictions             list (filters:
                                                 entity_id, model_id)
GET    /api/v1/registry/predictions/{id}        one prediction
PATCH  /api/v1/registry/predictions/{id}        update a prediction
DELETE /api/v1/registry/predictions/{id}        delete a prediction
POST   /api/v1/registry/outcomes                record an outcome
GET    /api/v1/registry/outcomes                list (filter:
                                                 prediction_id)
GET    /api/v1/registry/outcomes/{id}           one outcome
PATCH  /api/v1/registry/outcomes/{id}           update an outcome
DELETE /api/v1/registry/outcomes/{id}           delete an outcome
POST   /api/v1/registry/calibration             record a calibration
                                                 measurement
POST   /api/v1/registry/calibration/from-prediction
                                                 generate a
                                                 calibration record
                                                 from a prediction +
                                                 outcome pair (AT-503)
GET    /api/v1/registry/calibration             list (filter:
                                                 model_id)
GET    /api/v1/registry/calibration/{id}        one record
DELETE /api/v1/registry/calibration/{id}        delete a record
POST   /api/v1/registry/drift                   record drift
GET    /api/v1/registry/drift                   list (filters:
                                                 model_id, status)
GET    /api/v1/registry/drift/{id}              one drift record
DELETE /api/v1/registry/drift/{id}              delete a record

All endpoints require auth. Models, scores, calibration, and
drift are global registry records (any authenticated user may
read them); predictions and outcomes are entity-scoped — reads
follow the Sprint 1 rule (own entities plus shared ones) and
writes require owning the entity.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.registry import DriftStatus, ModelStatus, ValidationLevel
from app.models.user import User
from app.schemas.registry import (
    CalibrationCreate,
    CalibrationFromPredictionRequest,
    CalibrationOut,
    DriftCreate,
    DriftOut,
    ModelCreate,
    ModelOut,
    ModelUpdate,
    ModelVersionOut,
    OutcomeCreate,
    OutcomeOut,
    OutcomeUpdate,
    PredictionCreate,
    PredictionOut,
    PredictionUpdate,
    ScoreCreate,
    ScoreOut,
    ScoreUpdate,
)
from app.services.registry_service import (
    assert_outcome_access,
    assert_outcome_owner,
    assert_prediction_access,
    assert_prediction_owner,
    create_calibration,
    create_calibration_from_prediction,
    create_drift,
    create_model,
    create_outcome,
    create_prediction,
    create_score,
    delete_calibration,
    delete_drift,
    delete_model,
    delete_outcome,
    delete_prediction,
    delete_score,
    get_calibration_or_404,
    get_drift_or_404,
    get_model_or_404,
    get_outcome_or_404,
    get_prediction_or_404,
    get_score_or_404,
    list_calibration,
    list_drift,
    list_model_versions,
    list_models,
    list_outcomes,
    list_predictions,
    list_scores,
    update_model,
    update_outcome,
    update_prediction,
    update_score,
)

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


# --- Model Registry ---
@router.post(
    "/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED
)
async def register_model(
    payload: ModelCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register a model (AT-501). Writes an audit entry."""
    model = await create_model(db, current_user, payload)
    await db.commit()
    await db.refresh(model)
    return model


@router.get("/models", response_model=list[ModelOut])
async def list_models_endpoint(
    validation_level: ValidationLevel | None = Query(default=None),
    status_filter: ModelStatus | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Registered models, optionally filtered by validation
    level or status."""
    return await list_models(
        db,
        validation_level=(
            validation_level.value if validation_level is not None else None
        ),
        status_filter=(
            status_filter.value if status_filter is not None else None
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/models/{model_id}", response_model=ModelOut)
async def get_model(
    model_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One registered model."""
    return await get_model_or_404(db, model_id)


@router.get(
    "/models/{model_id}/versions",
    response_model=list[ModelVersionOut],
)
async def get_model_versions(
    model_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A model's preserved previous versions, newest first
    (AT-506)."""
    model = await get_model_or_404(db, model_id)
    return await list_model_versions(db, model)


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model_endpoint(
    model_id: int,
    payload: ModelUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a model (AT-504). A version change preserves the
    previous version in the version history (AT-506)."""
    model = await get_model_or_404(db, model_id)
    model = await update_model(db, current_user, model, payload)
    await db.commit()
    await db.refresh(model)
    return model


@router.delete(
    "/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_model_endpoint(
    model_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a model. Its version history, predictions,
    calibration records, and drift records cascade."""
    model = await get_model_or_404(db, model_id)
    await delete_model(db, current_user, model)
    await db.commit()


# --- Score Registry ---
@router.post(
    "/scores", response_model=ScoreOut, status_code=status.HTTP_201_CREATED
)
async def register_score(
    payload: ScoreCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register a score. Writes an audit entry."""
    score = await create_score(db, current_user, payload)
    await db.commit()
    await db.refresh(score)
    return score


@router.get("/scores", response_model=list[ScoreOut])
async def list_scores_endpoint(
    validation_level: ValidationLevel | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Registered scores, optionally filtered by validation
    level."""
    return await list_scores(
        db,
        validation_level=(
            validation_level.value if validation_level is not None else None
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/scores/{score_name}", response_model=ScoreOut)
async def get_score(
    score_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One registered score — the source of truth for its
    current formula, version, and status."""
    return await get_score_or_404(db, score_name)


@router.patch("/scores/{score_name}", response_model=ScoreOut)
async def update_score_endpoint(
    score_name: str,
    payload: ScoreUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a score. score_name is the key and cannot be
    renamed."""
    score = await get_score_or_404(db, score_name)
    score = await update_score(db, current_user, score, payload)
    await db.commit()
    await db.refresh(score)
    return score


@router.delete(
    "/scores/{score_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_score_endpoint(
    score_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a score."""
    score = await get_score_or_404(db, score_name)
    await delete_score(db, current_user, score)
    await db.commit()


# --- Prediction Registry ---
@router.post(
    "/predictions",
    response_model=PredictionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_prediction_endpoint(
    payload: PredictionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store a hypothesis (AT-502). The caller must own the
    entity; the model must exist. Writes an audit entry."""
    pred = await create_prediction(db, current_user, payload)
    await db.commit()
    await db.refresh(pred)
    return pred


@router.get("/predictions", response_model=list[PredictionOut])
async def list_predictions_endpoint(
    entity_id: int | None = Query(default=None),
    model_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Predictions on entities the caller may read, optionally
    filtered by entity or model."""
    return await list_predictions(
        db,
        current_user,
        entity_id=entity_id,
        model_id=model_id,
        limit=limit,
        offset=offset,
    )


@router.get("/predictions/{prediction_id}", response_model=PredictionOut)
async def get_prediction(
    prediction_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One prediction."""
    pred = await get_prediction_or_404(db, prediction_id)
    await assert_prediction_access(db, pred, current_user)
    return pred


@router.patch(
    "/predictions/{prediction_id}", response_model=PredictionOut
)
async def update_prediction_endpoint(
    prediction_id: int,
    payload: PredictionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a prediction. The caller must own the entity."""
    pred = await get_prediction_or_404(db, prediction_id)
    await assert_prediction_owner(db, pred, current_user)
    pred = await update_prediction(db, current_user, pred, payload)
    await db.commit()
    await db.refresh(pred)
    return pred


@router.delete(
    "/predictions/{prediction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_prediction_endpoint(
    prediction_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a prediction. Its outcomes cascade."""
    pred = await get_prediction_or_404(db, prediction_id)
    await assert_prediction_owner(db, pred, current_user)
    await delete_prediction(db, current_user, pred)
    await db.commit()


# --- Outcome Registry ---
@router.post(
    "/outcomes",
    response_model=OutcomeOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_outcome(
    payload: OutcomeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record an actual result (AT-502). One outcome per
    prediction — a second outcome is rejected with 409, never
    silently overwritten. Writes an audit entry."""
    outcome = await create_outcome(db, current_user, payload)
    await db.commit()
    await db.refresh(outcome)
    return outcome


@router.get("/outcomes", response_model=list[OutcomeOut])
async def list_outcomes_endpoint(
    prediction_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Outcomes on predictions the caller may read, optionally
    filtered by prediction."""
    return await list_outcomes(
        db,
        current_user,
        prediction_id=prediction_id,
        limit=limit,
        offset=offset,
    )


@router.get("/outcomes/{outcome_id}", response_model=OutcomeOut)
async def get_outcome(
    outcome_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One outcome."""
    outcome = await get_outcome_or_404(db, outcome_id)
    await assert_outcome_access(db, outcome, current_user)
    return outcome


@router.patch("/outcomes/{outcome_id}", response_model=OutcomeOut)
async def update_outcome_endpoint(
    outcome_id: int,
    payload: OutcomeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an outcome. The caller must own the entity."""
    outcome = await get_outcome_or_404(db, outcome_id)
    await assert_outcome_owner(db, outcome, current_user)
    outcome = await update_outcome(db, current_user, outcome, payload)
    await db.commit()
    await db.refresh(outcome)
    return outcome


@router.delete(
    "/outcomes/{outcome_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_outcome_endpoint(
    outcome_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an outcome."""
    outcome = await get_outcome_or_404(db, outcome_id)
    await assert_outcome_owner(db, outcome, current_user)
    await delete_outcome(db, current_user, outcome)
    await db.commit()


# --- Calibration Registry ---
@router.post(
    "/calibration",
    response_model=CalibrationOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_calibration(
    payload: CalibrationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record a calibration measurement. CE = Observed -
    Predicted is computed server-side; a client-supplied error
    is ignored. Writes an audit entry."""
    record = await create_calibration(db, current_user, payload)
    await db.commit()
    await db.refresh(record)
    return record


@router.post(
    "/calibration/from-prediction",
    response_model=CalibrationOut,
    status_code=status.HTTP_201_CREATED,
)
async def calibration_from_prediction(
    payload: CalibrationFromPredictionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a calibration record from a prediction +
    outcome pair (AT-503). The outcome must belong to the
    prediction; the model comes from the prediction; the caller
    must own the entity."""
    record = await create_calibration_from_prediction(
        db, current_user, payload
    )
    await db.commit()
    await db.refresh(record)
    return record


@router.get("/calibration", response_model=list[CalibrationOut])
async def list_calibration_endpoint(
    model_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Calibration records, optionally filtered by model."""
    return await list_calibration(
        db, model_id=model_id, limit=limit, offset=offset
    )


@router.get("/calibration/{record_id}", response_model=CalibrationOut)
async def get_calibration(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One calibration record."""
    return await get_calibration_or_404(db, record_id)


@router.delete(
    "/calibration/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_calibration_endpoint(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a calibration record."""
    record = await get_calibration_or_404(db, record_id)
    await delete_calibration(db, current_user, record)
    await db.commit()


# --- Drift Registry ---
@router.post(
    "/drift",
    response_model=DriftOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_drift(
    payload: DriftCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record drift (AT-507). change = current - baseline and
    status are computed server-side. Writes an audit entry."""
    record = await create_drift(db, current_user, payload)
    await db.commit()
    await db.refresh(record)
    return record


@router.get("/drift", response_model=list[DriftOut])
async def list_drift_endpoint(
    model_id: int | None = Query(default=None),
    status_filter: DriftStatus | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Drift records, optionally filtered by model or status."""
    return await list_drift(
        db,
        model_id=model_id,
        status_filter=(
            status_filter.value if status_filter is not None else None
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/drift/{record_id}", response_model=DriftOut)
async def get_drift(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One drift record."""
    return await get_drift_or_404(db, record_id)


@router.delete(
    "/drift/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_drift_endpoint(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a drift record."""
    record = await get_drift_or_404(db, record_id)
    await delete_drift(db, current_user, record)
    await db.commit()
