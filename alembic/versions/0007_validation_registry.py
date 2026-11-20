"""Add the Validation Registry tables and seed the nine models.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20

Engine v1 Sprint 5 (Validation Engine): the six registries —
Model Registry, Score Registry, Prediction Registry, Outcome
Registry, Calibration Registry, Drift Registry — plus the Model
Version History that preserves a previous version whenever a
model's version is updated (AT-506).

The seed inserts the nine named models of Engineering Order 005
into the Model Registry and the matching nine entries into the
Score Registry, all at validation level Experimental, the honest
current state per the Calibration & Validation Framework v1.0,
Appendix A. The seed list in app/services/registry_service.py
(SEED_MODELS) is the in-code source of the same list; the values
here must match it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# The nine named models (Engineering Order 005), all
# Experimental, all active. Must match SEED_MODELS in
# app/services/registry_service.py.
SEED = [
    {
        "name": "GP$ Index",
        "version": "1.0",
        "description": (
            "The complete ledger of life — the Master Index over "
            "the state vector. No Freedom GP$ score has reached "
            "Validated: no real-world validation data exists yet."
        ),
        "formula": (
            "Master Index — weighted formula over the state "
            "vector Ω(t); Master Index coefficients uncalibrated "
            "(Mathematical Framework v1.0)."
        ),
    },
    {
        "name": "Prosperity Score",
        "version": "1.0",
        "description": (
            "The Unified Prosperity Equation output. No Freedom "
            "GP$ score has reached Validated: no real-world "
            "validation data exists yet."
        ),
        "formula": (
            "P = (F+H+S+R+L) × (T+I+M) / E (Unified Prosperity "
            "Equation, Mathematical Framework v1.0)."
        ),
    },
    {
        "name": "Trust Score",
        "version": "1.0",
        "description": (
            "Trust Score, as defined in the Mathematical "
            "Framework v1.0. No Freedom GP$ score has reached "
            "Validated: no real-world validation data exists yet."
        ),
        "formula": (
            "Trust Score (Mathematical Framework v1.0). No Engine "
            "v1 reference implementation yet — this Score "
            "Registry entry is the formula record; update here "
            "first."
        ),
    },
    {
        "name": "Opportunity Score",
        "version": "1.0",
        "description": (
            "Opportunity Score, as defined in the Mathematical "
            "Framework v1.0. No Freedom GP$ score has reached "
            "Validated: no real-world validation data exists yet."
        ),
        "formula": (
            "Opportunity Score (Mathematical Framework v1.0). No "
            "Engine v1 reference implementation yet — this Score "
            "Registry entry is the formula record; update here "
            "first."
        ),
    },
    {
        "name": "Hidden Asset Multiplier",
        "version": "1.0",
        "description": (
            "Formula defined; computed by the v1 reference "
            "implementation on illustrative inputs. Conversion to "
            "observable value not yet demonstrated."
        ),
        "formula": (
            "HAM = (IP + Skills + Licenses + Network + Brand + "
            "Education + Future Royalties) / Reported Net Worth "
            "(Shadow Ledger engine, app/engines/shadow_ledger.py)."
        ),
    },
    {
        "name": "Stinnes Score",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "formula": (
            "SS = (Cash Flow Assets + Appreciating Assets + "
            "Hidden Assets) / (Debt Principal + Debt Carry Cost "
            "+ Debt Risk) (Stinnes engine, "
            "app/engines/stinnes.py)."
        ),
    },
    {
        "name": "Asset Flipper Score",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "formula": (
            "AFS = (Productive Assets + Hidden Assets) / Total "
            "Assets × 100 (Asset Flipper engine, "
            "app/engines/asset_flipper.py)."
        ),
    },
    {
        "name": "Anti-Fragility Index",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "formula": (
            "AFI = Stress Scenario Net Worth / Current Net Worth "
            "(Anti-Fragility engine, "
            "app/engines/anti_fragility.py)."
        ),
    },
    {
        "name": "Regulatory Friction Score",
        "version": "1.0",
        "description": (
            "Formula and bands defined; computed by the v1 "
            "reference implementation on illustrative inputs. No "
            "real-world validation data exists."
        ),
        "formula": (
            "RFS = Ownership Restrictions + Tax Friction + "
            "Capital Controls + Liquidity Restrictions + "
            "Compliance Burden (Regulatory Friction engine, "
            "app/engines/regulatory_friction.py)."
        ),
    },
]


def upgrade() -> None:
    op.create_table(
        "registry_models",
        sa.Column("model_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "version",
            sa.String(length=50),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "validation_level",
            sa.String(length=50),
            nullable=False,
            server_default="Experimental",
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="active",
        ),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_registry_models_name"),
    )
    op.create_index(
        "ix_registry_models_model_id", "registry_models", ["model_id"]
    )
    op.create_index(
        "ix_registry_models_name", "registry_models", ["name"]
    )
    op.create_index(
        "ix_registry_models_validation_level",
        "registry_models",
        ["validation_level"],
    )
    op.create_index(
        "ix_registry_models_status", "registry_models", ["status"]
    )

    op.create_table(
        "model_version_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_models.model_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "validation_level", sa.String(length=50), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_model_version_history_id",
        "model_version_history",
        ["id"],
    )
    op.create_index(
        "ix_model_version_history_model_id",
        "model_version_history",
        ["model_id"],
    )

    op.create_table(
        "registry_scores",
        sa.Column(
            "score_name", sa.String(length=200), primary_key=True
        ),
        sa.Column("current_formula", sa.Text(), nullable=False),
        sa.Column(
            "current_status",
            sa.String(length=50),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "validation_level",
            sa.String(length=50),
            nullable=False,
            server_default="Experimental",
        ),
        sa.Column(
            "active_version",
            sa.String(length=50),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_registry_scores_score_name",
        "registry_scores",
        ["score_name"],
    )
    op.create_index(
        "ix_registry_scores_validation_level",
        "registry_scores",
        ["validation_level"],
    )

    op.create_table(
        "registry_predictions",
        sa.Column("prediction_id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_models.model_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "prediction_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "forecast_horizon", sa.String(length=50), nullable=False
        ),
        sa.Column("predicted_outcome", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_registry_predictions_prediction_id",
        "registry_predictions",
        ["prediction_id"],
    )
    op.create_index(
        "ix_registry_predictions_entity_id",
        "registry_predictions",
        ["entity_id"],
    )
    op.create_index(
        "ix_registry_predictions_model_id",
        "registry_predictions",
        ["model_id"],
    )

    op.create_table(
        "registry_outcomes",
        sa.Column("outcome_id", sa.Integer(), primary_key=True),
        sa.Column(
            "prediction_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_predictions.prediction_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("actual_result", sa.JSON(), nullable=False),
        sa.Column(
            "recorded_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_registry_outcomes_outcome_id",
        "registry_outcomes",
        ["outcome_id"],
    )
    op.create_index(
        "ix_registry_outcomes_prediction_id",
        "registry_outcomes",
        ["prediction_id"],
    )

    op.create_table(
        "registry_calibration",
        sa.Column("record_id", sa.Integer(), primary_key=True),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_models.model_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=False),
        sa.Column("error", sa.Float(), nullable=False),
        sa.Column(
            "prediction_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_predictions.prediction_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "outcome_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_outcomes.outcome_id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_registry_calibration_record_id",
        "registry_calibration",
        ["record_id"],
    )
    op.create_index(
        "ix_registry_calibration_model_id",
        "registry_calibration",
        ["model_id"],
    )
    op.create_index(
        "ix_registry_calibration_prediction_id",
        "registry_calibration",
        ["prediction_id"],
    )
    op.create_index(
        "ix_registry_calibration_outcome_id",
        "registry_calibration",
        ["outcome_id"],
    )

    op.create_table(
        "registry_drift",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey(
                "registry_models.model_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("metric", sa.String(length=200), nullable=False),
        sa.Column("baseline", sa.Float(), nullable=False),
        sa.Column("current", sa.Float(), nullable=False),
        sa.Column("change", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_registry_drift_id", "registry_drift", ["id"]
    )
    op.create_index(
        "ix_registry_drift_model_id", "registry_drift", ["model_id"]
    )
    op.create_index(
        "ix_registry_drift_status", "registry_drift", ["status"]
    )

    # Seed the nine named models + nine score entries, all
    # Experimental, all active. bulk_insert works in both online
    # and offline (alembic upgrade head --sql) modes.
    models_table = sa.table(
        "registry_models",
        sa.column("name", sa.String),
        sa.column("version", sa.String),
        sa.column("description", sa.Text),
        sa.column("validation_level", sa.String),
        sa.column("status", sa.String),
    )
    op.bulk_insert(
        models_table,
        [
            {
                "name": entry["name"],
                "version": entry["version"],
                "description": entry["description"],
                "validation_level": "Experimental",
                "status": "active",
            }
            for entry in SEED
        ],
    )
    scores_table = sa.table(
        "registry_scores",
        sa.column("score_name", sa.String),
        sa.column("current_formula", sa.Text),
        sa.column("current_status", sa.String),
        sa.column("validation_level", sa.String),
        sa.column("active_version", sa.String),
    )
    op.bulk_insert(
        scores_table,
        [
            {
                "score_name": entry["name"],
                "current_formula": entry["formula"],
                "current_status": "active",
                "validation_level": "Experimental",
                "active_version": "1.0",
            }
            for entry in SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_registry_drift_status", table_name="registry_drift")
    op.drop_index(
        "ix_registry_drift_model_id", table_name="registry_drift"
    )
    op.drop_index("ix_registry_drift_id", table_name="registry_drift")
    op.drop_table("registry_drift")

    op.drop_index(
        "ix_registry_calibration_outcome_id",
        table_name="registry_calibration",
    )
    op.drop_index(
        "ix_registry_calibration_prediction_id",
        table_name="registry_calibration",
    )
    op.drop_index(
        "ix_registry_calibration_model_id",
        table_name="registry_calibration",
    )
    op.drop_index(
        "ix_registry_calibration_record_id",
        table_name="registry_calibration",
    )
    op.drop_table("registry_calibration")

    op.drop_index(
        "ix_registry_outcomes_prediction_id",
        table_name="registry_outcomes",
    )
    op.drop_index(
        "ix_registry_outcomes_outcome_id",
        table_name="registry_outcomes",
    )
    op.drop_table("registry_outcomes")

    op.drop_index(
        "ix_registry_predictions_model_id",
        table_name="registry_predictions",
    )
    op.drop_index(
        "ix_registry_predictions_entity_id",
        table_name="registry_predictions",
    )
    op.drop_index(
        "ix_registry_predictions_prediction_id",
        table_name="registry_predictions",
    )
    op.drop_table("registry_predictions")

    op.drop_index(
        "ix_registry_scores_validation_level",
        table_name="registry_scores",
    )
    op.drop_index(
        "ix_registry_scores_score_name", table_name="registry_scores"
    )
    op.drop_table("registry_scores")

    op.drop_index(
        "ix_model_version_history_model_id",
        table_name="model_version_history",
    )
    op.drop_index(
        "ix_model_version_history_id",
        table_name="model_version_history",
    )
    op.drop_table("model_version_history")

    op.drop_index(
        "ix_registry_models_status", table_name="registry_models"
    )
    op.drop_index(
        "ix_registry_models_validation_level",
        table_name="registry_models",
    )
    op.drop_index(
        "ix_registry_models_name", table_name="registry_models"
    )
    op.drop_index(
        "ix_registry_models_model_id", table_name="registry_models"
    )
    op.drop_table("registry_models")
