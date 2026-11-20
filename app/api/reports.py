"""Report endpoints — create and list a user's reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.report import Report
from app.models.user import User

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class ReportCreate(BaseModel):
    title: str
    report_type: str
    content: dict = Field(default_factory=dict)
    summary: str = ""


class ReportOut(BaseModel):
    id: int
    user_id: int
    title: str
    report_type: str
    content: dict
    summary: str

    model_config = {"from_attributes": True}


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Report:
    """Store a report for the current user."""
    report = Report(user_id=current_user.id, **payload.model_dump())
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@router.get("", response_model=list[ReportOut])
async def list_reports(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Report]:
    """List the current user's reports."""
    result = await db.execute(
        select(Report).where(Report.user_id == current_user.id)
    )
    return list(result.scalars().all())
