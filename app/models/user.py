"""User model — the account owner (v1: one user per account)."""

from __future__ import annotations

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    profile = relationship("Profile", back_populates="user", uselist=False)
    assets = relationship("Asset", back_populates="user")
    liabilities = relationship("Liability", back_populates="user")
    hidden_assets = relationship("HiddenAsset", back_populates="user")
    cash_flows = relationship("CashFlow", back_populates="user")
    score_snapshots = relationship("ScoreSnapshot", back_populates="user")
    scenarios = relationship("Scenario", back_populates="user")
    reports = relationship("Report", back_populates="user")
    recommendations = relationship("Recommendation", back_populates="user")
    state_vectors = relationship("StateVector", back_populates="user")
