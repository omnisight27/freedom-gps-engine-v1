"""HiddenAsset model — off-balance-sheet assets in the Shadow Ledger
(IP, skills, licenses, network, brand, education, future royalties, ...)."""

from __future__ import annotations

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class HiddenAsset(Base):
    __tablename__ = "hidden_assets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    estimated_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user = relationship("User", back_populates="hidden_assets")
