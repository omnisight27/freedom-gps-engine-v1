"""OmniSightSignal model — an intelligence signal from the OmniSight layer
(economic, market, geopolitical, regulatory, sovereign, behavioral)."""

from __future__ import annotations

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OmniSightSignal(Base):
    __tablename__ = "omnisight_signals"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    recorded_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
