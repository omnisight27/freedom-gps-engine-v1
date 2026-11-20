"""Add the Knowledge Vault tables and the audit log.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20

Engine v1 Sprint 2 (formal definition): the unified KnowledgeAsset
record with lineage (created_by, source, version), EvidenceRecord
attached to an asset, the per-registry Skill, Certification,
FamilyRecord, and LegacyAsset tables, and the audit_log table for
Epic 2.8 events. No Opportunity Repository in this sprint (deferred
to Sprint 4) and no separate MemoryRecord model (memory is covered
by KnowledgeAsset categories).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _timestamps():
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "knowledge_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="active",
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=500), nullable=True),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1"
        ),
        *_timestamps(),
    )
    op.create_index("ix_knowledge_assets_id", "knowledge_assets", ["id"])
    op.create_index(
        "ix_knowledge_assets_entity_id", "knowledge_assets", ["entity_id"]
    )
    op.create_index(
        "ix_knowledge_assets_category", "knowledge_assets", ["category"]
    )
    op.create_index(
        "ix_knowledge_assets_created_by", "knowledge_assets", ["created_by"]
    )

    op.create_table(
        "evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "knowledge_asset_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_evidence_records_id", "evidence_records", ["id"])
    op.create_index(
        "ix_evidence_records_knowledge_asset_id",
        "evidence_records",
        ["knowledge_asset_id"],
    )
    op.create_index(
        "ix_evidence_records_source_type", "evidence_records", ["source_type"]
    )

    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_asset_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("proficiency", sa.String(length=50), nullable=True),
        sa.Column("years_experience", sa.Float(), nullable=True),
        sa.Column(
            "verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        *_timestamps(),
    )
    op.create_index("ix_skills_id", "skills", ["id"])
    op.create_index("ix_skills_entity_id", "skills", ["entity_id"])
    op.create_index(
        "ix_skills_knowledge_asset_id", "skills", ["knowledge_asset_id"]
    )
    op.create_index("ix_skills_verified", "skills", ["verified"])

    op.create_table(
        "certifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("credential_id", sa.String(length=200), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_certifications_id", "certifications", ["id"])
    op.create_index(
        "ix_certifications_entity_id", "certifications", ["entity_id"]
    )

    op.create_table(
        "family_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("date_recorded", sa.Date(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_family_records_id", "family_records", ["id"])
    op.create_index(
        "ix_family_records_entity_id", "family_records", ["entity_id"]
    )
    op.create_index(
        "ix_family_records_record_type", "family_records", ["record_type"]
    )

    op.create_table(
        "legacy_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("asset_type", sa.String(length=50), nullable=False),
        sa.Column("estimated_impact", sa.Float(), nullable=True),
        sa.Column("preservation_status", sa.String(length=100), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_legacy_assets_id", "legacy_assets", ["id"])
    op.create_index(
        "ix_legacy_assets_entity_id", "legacy_assets", ["entity_id"]
    )
    op.create_index(
        "ix_legacy_assets_asset_type", "legacy_assets", ["asset_type"]
    )
    op.create_index(
        "ix_legacy_assets_preservation_status",
        "legacy_assets",
        ["preservation_status"],
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=100), nullable=True),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("record_type", sa.String(length=100), nullable=True),
        sa.Column("record_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=200), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("details", sa.Text(), nullable=True),
    )
    op.create_index("ix_audit_log_id", "audit_log", ["id"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_entity_id", "audit_log", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_entity_id", table_name="audit_log")
    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_index("ix_audit_log_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(
        "ix_legacy_assets_preservation_status", table_name="legacy_assets"
    )
    op.drop_index("ix_legacy_assets_asset_type", table_name="legacy_assets")
    op.drop_index("ix_legacy_assets_entity_id", table_name="legacy_assets")
    op.drop_index("ix_legacy_assets_id", table_name="legacy_assets")
    op.drop_table("legacy_assets")
    op.drop_index("ix_family_records_record_type", table_name="family_records")
    op.drop_index("ix_family_records_entity_id", table_name="family_records")
    op.drop_index("ix_family_records_id", table_name="family_records")
    op.drop_table("family_records")
    op.drop_index(
        "ix_certifications_entity_id", table_name="certifications"
    )
    op.drop_index("ix_certifications_id", table_name="certifications")
    op.drop_table("certifications")
    op.drop_index("ix_skills_verified", table_name="skills")
    op.drop_index("ix_skills_knowledge_asset_id", table_name="skills")
    op.drop_index("ix_skills_entity_id", table_name="skills")
    op.drop_index("ix_skills_id", table_name="skills")
    op.drop_table("skills")
    op.drop_index(
        "ix_evidence_records_source_type", table_name="evidence_records"
    )
    op.drop_index(
        "ix_evidence_records_knowledge_asset_id",
        table_name="evidence_records",
    )
    op.drop_index("ix_evidence_records_id", table_name="evidence_records")
    op.drop_table("evidence_records")
    op.drop_index(
        "ix_knowledge_assets_created_by", table_name="knowledge_assets"
    )
    op.drop_index(
        "ix_knowledge_assets_category", table_name="knowledge_assets"
    )
    op.drop_index(
        "ix_knowledge_assets_entity_id", table_name="knowledge_assets"
    )
    op.drop_index("ix_knowledge_assets_id", table_name="knowledge_assets")
    op.drop_table("knowledge_assets")
