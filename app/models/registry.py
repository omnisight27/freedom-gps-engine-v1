"""Validation Registry models — Engine v1 Sprint 5 (Validation Engine).

Sprint Goal: implement the operational mechanisms defined by the
Calibration & Validation Framework v1.0. THE MISSION IS NOT TO
VALIDATE MODELS YET. The mission is to build the machinery that
allows validation to occur. Without Sprint 5, Opportunity
detection is just an algorithm; with Sprint 5, Opportunity
detection becomes a hypothesis that can be tested.

Six registries:

- Model Registry (`RegistryModel`): every model must exist in a
  registry. Seeded with the nine named models (GP$ Index,
  Prosperity Score, Trust Score, Opportunity Score, Hidden Asset
  Multiplier, Stinnes Score, Asset Flipper Score, Anti-Fragility
  Index, Regulatory Friction Score) — all at validation level
  Experimental, the honest current state per the Framework's
  Appendix A ("No Freedom GP$ score has yet reached Level 3
  (Validated) or Level 4 (Institutional), because no real-world
  validation data exists for them").
- Score Registry (`ScoreRegistry`): the separate runtime
  registry and the single source of truth for a score's current
  formula, version, and status. `current_formula` is the formula
  as a text record, NOT an implementation.
- Prediction Registry (`Prediction`): hypotheses. A prediction
  stores what a model said would happen; the outcome is recorded
  later. Nothing is validated yet — this is collecting evidence.
- Outcome Registry (`Outcome`): actual results, linked to their
  prediction. One outcome per prediction: a second outcome for
  the same prediction is rejected (409), never silently
  overwritten.
- Calibration Registry (`CalibrationRecord`): observed vs
  predicted, with CE = Observed - Predicted computed
  server-side on create, never client-supplied.
- Drift Registry (`DriftRecord`): foundational drift tracking
  only. `change` is computed server-side (current - baseline)
  and `status` is derived from the relative change: normal at
  <= 5%, warning at > 5% and <= 20%, critical above 20% (see
  registry_service.drift_status).

`ModelVersionHistory` preserves the previous version whenever a
model's version is updated (AT-506): the prior version,
description, and validation level are retrievable at
GET /registry/models/{model_id}/versions.

Lineage: every registry record's create/update/delete writes an
AuditLog entry (AT-505). Predictions link to their entity and
model; outcomes link to their prediction; calibration records
link to their model and, when generated from a prediction +
outcome, to that prediction and outcome.

Out of scope for Sprint 5 (do not add): new scores, new
economic models, actual validation of any model, advanced drift
analytics, forecasting, simulation, AI reasoning. No engine math
changes; Master Index coefficients untouched.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ValidationLevel(str, enum.Enum):
    """The five validation levels of the Calibration &
    Validation Framework v1.0, section 4.1. A measurement moves
    up the hierarchy only by evidence, never by assertion."""

    CONCEPTUAL = "Conceptual"
    PROTOTYPE = "Prototype"
    EXPERIMENTAL = "Experimental"
    VALIDATED = "Validated"
    INSTITUTIONAL = "Institutional"


class ModelStatus(str, enum.Enum):
    """The lifecycle of a registered model."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class DriftStatus(str, enum.Enum):
    """The status of a drift record, derived server-side from
    the relative change (see registry_service.drift_status)."""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class RegistryModel(Base):
    """One registered model — the Model Registry.

    `model_id` is the primary key (the order's field name).
    `version` is a string (e.g. "1.0"). `validation_level`
    defaults to Experimental: no Freedom GP$ score has reached
    Validated, because no real-world validation data exists yet.
    """

    __tablename__ = "registry_models"

    model_id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True
    )
    version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="1.0"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_level: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ValidationLevel.EXPERIMENTAL.value,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ModelStatus.ACTIVE.value,
        index=True,
    )
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions = relationship(
        "ModelVersionHistory",
        back_populates="model",
        cascade="all, delete-orphan",
    )
    predictions = relationship(
        "Prediction",
        back_populates="model",
        cascade="all, delete-orphan",
    )
    calibration_records = relationship(
        "CalibrationRecord",
        back_populates="model",
        cascade="all, delete-orphan",
    )
    drift_records = relationship(
        "DriftRecord",
        back_populates="model",
        cascade="all, delete-orphan",
    )


class ModelVersionHistory(Base):
    """One preserved previous version of a model (AT-506).

    Written whenever a model's version is updated: the prior
    version, description, and validation level are snapshotted
    here before the model row changes, so no version is ever
    lost.
    """

    __tablename__ = "model_version_history"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("registry_models.model_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_level: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    model = relationship("RegistryModel", back_populates="versions")


class ScoreRegistry(Base):
    """One runtime score — the Score Registry.

    The single source of truth for a score's current formula,
    version, and status. `score_name` is the natural key (and
    the lookup key at GET /registry/scores/{score_name}).
    `current_formula` is the formula as a text record, NOT an
    implementation. A score entry is linked by name to its Model
    Registry entry.
    """

    __tablename__ = "registry_scores"

    score_name: Mapped[str] = mapped_column(
        String(200), primary_key=True, index=True
    )
    current_formula: Mapped[str] = mapped_column(Text, nullable=False)
    current_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active"
    )
    validation_level: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ValidationLevel.EXPERIMENTAL.value,
        index=True,
    )
    active_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="1.0"
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Prediction(Base):
    """One stored hypothesis — the Prediction Registry.

    `forecast_horizon` is a duration string ("30d", "90d",
    "1y"). `predicted_outcome` is JSON: a number, or an object
    carrying the predicted value (the from-prediction
    calibration path reads a top-level number or a "value"
    key). The outcome is recorded later, as an Outcome row —
    nothing here is validated yet.
    """

    __tablename__ = "registry_predictions"

    prediction_id: Mapped[int] = mapped_column(
        primary_key=True, index=True
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("registry_models.model_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    prediction_date: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    forecast_horizon: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    predicted_outcome: Mapped[object] = mapped_column(
        JSON, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    entity = relationship("Entity")
    model = relationship("RegistryModel", back_populates="predictions")
    outcomes = relationship(
        "Outcome",
        back_populates="prediction",
        cascade="all, delete-orphan",
    )


class Outcome(Base):
    """One actual result — the Outcome Registry.

    Linked to its prediction. One outcome per prediction: a
    second outcome for the same prediction is rejected (409),
    never silently overwritten — the first recorded result is
    the evidence, and replacing it would rewrite history.
    """

    __tablename__ = "registry_outcomes"

    outcome_id: Mapped[int] = mapped_column(primary_key=True, index=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey(
            "registry_predictions.prediction_id", ondelete="CASCADE"
        ),
        index=True,
        nullable=False,
    )
    actual_result: Mapped[object] = mapped_column(JSON, nullable=False)
    recorded_date: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    prediction = relationship("Prediction", back_populates="outcomes")


class CalibrationRecord(Base):
    """One calibration measurement — the Calibration Registry.

    Calibration Error: CE = Observed - Predicted (Framework
    v1.0, section 5.3). `error` is computed server-side on
    create, never client-supplied. When generated from a
    prediction + outcome pair, `prediction_id` and `outcome_id`
    link the record to the evidence it came from.
    """

    __tablename__ = "registry_calibration"

    record_id: Mapped[int] = mapped_column(primary_key=True, index=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("registry_models.model_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[float] = mapped_column(Float, nullable=False)
    prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "registry_predictions.prediction_id", ondelete="SET NULL"
        ),
        index=True,
        nullable=True,
    )
    outcome_id: Mapped[int | None] = mapped_column(
        ForeignKey("registry_outcomes.outcome_id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    timestamp: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    model = relationship(
        "RegistryModel", back_populates="calibration_records"
    )
    prediction = relationship("Prediction")
    outcome = relationship("Outcome")


class DriftRecord(Base):
    """One foundational drift measurement — the Drift Registry.

    No advanced drift analytics in Sprint 5: this is the
    record that future analytics will read. `change` is
    computed server-side (current - baseline) and `status` is
    derived from the relative change (see
    registry_service.drift_status): normal at <= 5%, warning
    at > 5% and <= 20%, critical above 20%.
    """

    __tablename__ = "registry_drift"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("registry_models.model_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    metric: Mapped[str] = mapped_column(String(200), nullable=False)
    baseline: Mapped[float] = mapped_column(Float, nullable=False)
    current: Mapped[float] = mapped_column(Float, nullable=False)
    change: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=DriftStatus.NORMAL.value,
        index=True,
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    model = relationship("RegistryModel", back_populates="drift_records")
