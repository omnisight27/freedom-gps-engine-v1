"""StateVector model — one stored snapshot of the universal state vector Ω(t).

    Ω(t) = [F, H, S, R, L, T, M, I, E]

F Financial Capital, H Human Capital, S Social Capital, R Resource
Capital, L Legacy Capital, T Trust Capital, M Memory Capital,
I Intelligence Capital, E Entropy.

One row is one measurement of one user at one time. The time series of
rows is the ledger's trajectory through time — it is what the GP$ Master
Index and the Unified Prosperity Equation are computed from, and what
forecasting reads. The coefficients of the Master Index are NOT stored
here; they are learned statistically and supplied at computation time.
"""

from __future__ import annotations

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StateVector(Base):
    __tablename__ = "state_vectors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recorded_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # The nine state-vector variables. Financial capital may be negative
    # (liabilities can exceed assets); the rest are magnitudes, so the
    # API layer rejects negatives for them. All are nullable: a snapshot
    # may record only the variables that were measured.
    financial: Mapped[float | None] = mapped_column(Float, nullable=True)
    human: Mapped[float | None] = mapped_column(Float, nullable=True)
    social: Mapped[float | None] = mapped_column(Float, nullable=True)
    resource: Mapped[float | None] = mapped_column(Float, nullable=True)
    legacy: Mapped[float | None] = mapped_column(Float, nullable=True)
    trust: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory: Mapped[float | None] = mapped_column(Float, nullable=True)
    intelligence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entropy: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The Opportunity Vector: value not yet realized.
    opportunity: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    user = relationship("User", back_populates="state_vectors")
