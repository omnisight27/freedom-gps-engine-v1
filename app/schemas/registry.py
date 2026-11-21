"""Pydantic v2 request/response models for the Validation Engine.

Engine v1 Sprint 5. Enums are validated here; the models store
them as plain strings, matching the repo's style for type columns.
Calibration error and drift change/status are computed
server-side and are never accepted from the client — the create
schemas have no field for them, so a client-supplied value is
ignored and the server's value is what is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.registry import ModelStatus, ValidationLevel


# --- Model Registry ---
class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="1.0", min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=20000)
    validation_level: ValidationLevel = ValidationLevel.EXPERIMENTAL
    status: ModelStatus = ModelStatus.ACTIVE
    owner: str | None = Field(default=None, max_length=200)


class ModelUpdate(BaseModel):
    """PATCH body: only supplied, non-None values change (None
    means "no change", matching the repo's PATCH semantics). A
    version change preserves the previous version in the version
    history (AT-506)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    version: str | None = Field(
        default=None, min_length=1, max_length=50
    )
    description: str | None = Field(default=None, max_length=20000)
    validation_level: ValidationLevel | None = None
    status: ModelStatus | None = None
    owner: str | None = Field(default=None, max_length=200)


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model_id: int
    name: str
    version: str
    description: str | None
    validation_level: str
    status: str
    owner: str | None
    created_at: datetime
    updated_at: datetime


class ModelVersionOut(BaseModel):
    """One preserved previous version (AT-506)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    model_id: int
    version: str
    description: str | None
    validation_level: str | None
    created_at: datetime


# --- Score Registry ---
class ScoreCreate(BaseModel):
    score_name: str = Field(min_length=1, max_length=200)
    current_formula: str = Field(min_length=1, max_length=20000)
    current_status: str = Field(default="active", max_length=50)
    validation_level: ValidationLevel = ValidationLevel.EXPERIMENTAL
    active_version: str = Field(default="1.0", max_length=50)


class ScoreUpdate(BaseModel):
    """PATCH body: only supplied, non-None values change.
    score_name is the key and cannot be renamed — delete and
    re-create instead."""

    current_formula: str | None = Field(
        default=None, min_length=1, max_length=20000
    )
    current_status: str | None = Field(default=None, max_length=50)
    validation_level: ValidationLevel | None = None
    active_version: str | None = Field(default=None, max_length=50)


class ScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score_name: str
    current_formula: str
    current_status: str
    validation_level: str
    active_version: str
    created_at: datetime
    updated_at: datetime


# --- Prediction Registry ---
class PredictionCreate(BaseModel):
    entity_id: int
    model_id: int
    forecast_horizon: str = Field(
        min_length=1,
        max_length=50,
        description='A duration string, e.g. "30d", "90d", "1y".',
    )
    predicted_outcome: Any
    confidence: float = Field(ge=0, le=1)


class PredictionUpdate(BaseModel):
    """PATCH body: only supplied, non-None values change.
    entity_id and model_id are identity and cannot change."""

    forecast_horizon: str | None = Field(
        default=None, min_length=1, max_length=50
    )
    predicted_outcome: Any = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prediction_id: int
    entity_id: int
    model_id: int
    prediction_date: datetime
    forecast_horizon: str
    predicted_outcome: Any
    confidence: float
    created_at: datetime
    updated_at: datetime


# --- Outcome Registry ---
class OutcomeCreate(BaseModel):
    prediction_id: int
    actual_result: Any
    notes: str | None = Field(default=None, max_length=20000)


class OutcomeUpdate(BaseModel):
    """PATCH body: only supplied, non-None values change.
    prediction_id is identity and cannot change."""

    actual_result: Any = None
    notes: str | None = Field(default=None, max_length=20000)


class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome_id: int
    prediction_id: int
    actual_result: Any
    recorded_date: datetime
    notes: str | None
    created_at: datetime
    updated_at: datetime


# --- Calibration Registry ---
class CalibrationCreate(BaseModel):
    """No `error` field: CE = Observed - Predicted is computed
    server-side (AT-503). A client-supplied error is ignored."""

    model_id: int
    observed_value: float
    predicted_value: float
    prediction_id: int | None = None
    outcome_id: int | None = None


class CalibrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_id: int
    model_id: int
    observed_value: float
    predicted_value: float
    error: float
    prediction_id: int | None
    outcome_id: int | None
    timestamp: datetime


class CalibrationFromPredictionRequest(BaseModel):
    """Generate a calibration record from a prediction + outcome
    pair (AT-503). The outcome must belong to the prediction;
    the model comes from the prediction. The numeric values are
    read from the JSON: a top-level number, or an object with a
    numeric "value" key."""

    prediction_id: int
    outcome_id: int


# --- Drift Registry ---
class DriftCreate(BaseModel):
    """No `change` or `status` field: change is computed
    server-side (current - baseline) and status is derived from
    the relative change (AT-507)."""

    model_id: int
    metric: str = Field(min_length=1, max_length=200)
    baseline: float
    current: float


class DriftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    model_id: int
    metric: str
    baseline: float
    current: float
    change: float
    status: str
    created_at: datetime
