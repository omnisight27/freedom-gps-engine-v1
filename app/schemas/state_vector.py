"""Pydantic v2 request/response models for the state vector Ω(t).

The nine variables are F Financial, H Human, S Social, R Resource,
L Legacy, T Trust, M Memory, I Intelligence Capital, E Entropy.

Master Index coefficients are explicit inputs. Their defaults are
UNCALIBRATED PLACEHOLDERS (equal weight 1.0) — they are NOT measured
values. They must be learned statistically (regression, optimization
theory, machine learning, Bayesian inference, system dynamics) from
real user data before any Master Index is presented as calibrated.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- State vector snapshot ---
class StateVectorCreate(BaseModel):
    """Record one snapshot of the state vector. Financial capital may be
    negative (liabilities can exceed assets); the rest are magnitudes."""

    financial: float | None = None
    human: float | None = Field(default=None, ge=0)
    social: float | None = Field(default=None, ge=0)
    resource: float | None = Field(default=None, ge=0)
    legacy: float | None = Field(default=None, ge=0)
    trust: float | None = Field(default=None, ge=0)
    memory: float | None = Field(default=None, ge=0)
    intelligence: float | None = Field(default=None, ge=0)
    entropy: float | None = Field(default=None, ge=0)
    opportunity: float | None = Field(default=None, ge=0)
    notes: str = ""


class StateVectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    recorded_at: datetime
    financial: float | None
    human: float | None
    social: float | None
    resource: float | None
    legacy: float | None
    trust: float | None
    memory: float | None
    intelligence: float | None
    entropy: float | None
    opportunity: float | None
    notes: str


# --- GP$ Master Index ---
class MasterIndexCoefficients(BaseModel):
    """Weights for the GP$ Master Index.

    Defaults are UNCALIBRATED PLACEHOLDERS (equal weight 1.0). They are
    not measured values and must not be presented as calibrated.
    """

    alpha: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    beta: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    gamma: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    delta: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    epsilon: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    zeta: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    eta: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    theta: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")
    lam: float = Field(default=1.0, description="UNCALIBRATED PLACEHOLDER")


class MasterIndexRequest(BaseModel):
    """Compute the Master Index and the Unified Prosperity Equation from
    one state vector. Pure computation — no database, no auth."""

    financial: float = 0.0
    human: float = Field(default=0.0, ge=0)
    social: float = Field(default=0.0, ge=0)
    resource: float = Field(default=0.0, ge=0)
    legacy: float = Field(default=0.0, ge=0)
    trust: float = Field(default=0.0, ge=0)
    memory: float = Field(default=0.0, ge=0)
    intelligence: float = Field(default=0.0, ge=0)
    entropy: float = Field(default=0.0, ge=0)
    coefficients: MasterIndexCoefficients = Field(
        default_factory=MasterIndexCoefficients
    )


class MasterIndexResponse(BaseModel):
    master_index: float
    prosperity: float | None
    components: dict
    coefficients_used: dict
    calibrated: bool
    note: str
