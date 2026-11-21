"""Add the entity-resolution layer (Phase C.1).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20

Phase C.1 — Entity Resolution: the graph cannot discover
opportunities until it knows that multiple representations of
the same real-world thing are one entity (One Entity = One
Identity = One Node).

- entities gains canonical_name (the display form, first-seen
  spelling), aliases (JSON list of other known spellings), and
  confidence_score (the layer confidence that produced the
  canonical record). canonical_name is backfilled from the
  existing name — every pre-C.1 entity's canonical name is the
  name it already had.
- entity_resolution_reviews is the human review queue: an L4
  context candidate (confidence 0.70-0.89) never auto-attaches;
  it queues a row here, and the operator approves (merge) or
  rejects (suppress future re-queuing of the same pair).

MIGRATION-NUMBER NOTE: this revision takes "0008" to keep a
single linear chain from 0007. The Phase A.5 scope document
(phase-a5-historical-validation-backfill-scope-2026-11-20.md)
also plans a migration numbered 0008 (Prediction.source +
backfill_runs) — A.5 is scoped, not built, so that number is
only a plan. When A.5 executes, its planned migration must be
renumbered to 0009.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entities",
        sa.Column("canonical_name", sa.String(200), nullable=True),
    )
    # Backfill: every pre-C.1 entity's canonical name is the name
    # it already had.
    op.execute("UPDATE entities SET canonical_name = name")
    with op.batch_alter_table("entities") as batch_op:
        batch_op.alter_column("canonical_name", nullable=False)
    op.add_column(
        "entities",
        sa.Column(
            "aliases", sa.JSON, nullable=False, server_default="[]"
        ),
    )
    op.add_column(
        "entities",
        sa.Column(
            "confidence_score",
            sa.Float,
            nullable=False,
            server_default="1.0",
        ),
    )
    op.create_table(
        "entity_resolution_reviews",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposed_name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column(
            "matched_entity_id",
            sa.Integer,
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("layer", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("context", sa.JSON, nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("entity_resolution_reviews")
    with op.batch_alter_table("entities") as batch_op:
        batch_op.drop_column("confidence_score")
        batch_op.drop_column("aliases")
        batch_op.drop_column("canonical_name")
