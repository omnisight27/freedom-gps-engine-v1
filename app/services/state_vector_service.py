"""State Vector Service — record, read, and score an entity's Ω(t).

The service layer for state vectors measured against entities. It
reuses the existing Master Index engine (`app/engines/master_index.py`)
for all math — nothing here re-implements a formula, and the Master
Index coefficients are never changed here.

A snapshot records the ten state-vector values F, H, S, R, L, T, M, I,
E, O for one entity at one time. The recording user's id is stored as
the snapshot's user_id (the existing column, still required); the
entity is stored in the snapshot's entity_id (added in Sprint 1).
"""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.master_index import calculate_master_index
from app.models.entity import Entity
from app.models.state_vector import StateVector
from app.schemas.state_vector import MasterIndexCoefficients, StateVectorCreate


def _values_for_computation(snapshot: StateVector) -> dict[str, float]:
    """The ten variables as floats for the Master Index engine.

    Unmeasured variables count as 0.0 in the computation; the stored
    snapshot keeps its NULLs.
    """
    return {
        "financial": snapshot.financial if snapshot.financial is not None else 0.0,
        "human": snapshot.human if snapshot.human is not None else 0.0,
        "social": snapshot.social if snapshot.social is not None else 0.0,
        "resource": snapshot.resource if snapshot.resource is not None else 0.0,
        "legacy": snapshot.legacy if snapshot.legacy is not None else 0.0,
        "trust": snapshot.trust if snapshot.trust is not None else 0.0,
        "memory": snapshot.memory if snapshot.memory is not None else 0.0,
        "intelligence": (
            snapshot.intelligence if snapshot.intelligence is not None else 0.0
        ),
        "entropy": snapshot.entropy if snapshot.entropy is not None else 0.0,
    }


async def record_entity_state_vector(
    db: AsyncSession,
    *,
    user_id: int,
    entity: Entity,
    payload: StateVectorCreate,
) -> StateVector:
    """Record one snapshot of an entity's state vector Ω(t)."""
    snapshot = StateVector(
        user_id=user_id, entity_id=entity.id, **payload.model_dump()
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


async def get_entity_state_vector_history(
    db: AsyncSession,
    entity_id: int,
    limit: int = 50,
) -> list[StateVector]:
    """An entity's snapshots, latest first."""
    result = await db.execute(
        select(StateVector)
        .where(StateVector.entity_id == entity_id)
        .order_by(desc(StateVector.recorded_at), desc(StateVector.id))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_latest_entity_state_vector(
    db: AsyncSession, entity_id: int
) -> StateVector | None:
    """An entity's latest snapshot, or None when none is recorded."""
    result = await db.execute(
        select(StateVector)
        .where(StateVector.entity_id == entity_id)
        .order_by(desc(StateVector.recorded_at), desc(StateVector.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


def compute_master_index_for_snapshot(
    snapshot: StateVector,
    coefficients: MasterIndexCoefficients | None = None,
) -> dict:
    """GP$ Master Index + Unified Prosperity Equation for one stored
    snapshot. Delegates entirely to the existing engine; coefficients
    default to the uncalibrated placeholders, so the result always
    reports `calibrated: false` until real coefficients are supplied."""
    values = _values_for_computation(snapshot)
    return calculate_master_index(
        coefficients=(
            coefficients.model_dump() if coefficients is not None else None
        ),
        **values,
    )
