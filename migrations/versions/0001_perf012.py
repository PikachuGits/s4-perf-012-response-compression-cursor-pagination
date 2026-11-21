"""create perf012 list tables and keyset indexes

Revision ID: 0001_perf012
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_perf012"
down_revision = None
branch_labels = None
depends_on = None


COMMON_COLUMNS = [
    sa.Column("org_id", sa.Uuid(), nullable=False),
    sa.Column("site_id", sa.Uuid(), nullable=False),
    sa.Column("created_by", sa.Uuid(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.Uuid(), nullable=False),
        *COMMON_COLUMNS,
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("industry", sa.String(length=60), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_members_org_created_id", "members", ["org_id", "created_at", "id"])
    op.create_index(
        "ix_members_org_status_created_id",
        "members",
        ["org_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_members_org_tier_created_id", "members", ["org_id", "tier", "created_at", "id"]
    )

    op.create_table(
        "interactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        *COMMON_COLUMNS,
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interactions_org_occurred_id", "interactions", ["org_id", "occurred_at", "id"]
    )
    op.create_index(
        "ix_interactions_org_member_occurred_id",
        "interactions",
        ["org_id", "member_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_interactions_org_channel_occurred_id",
        "interactions",
        ["org_id", "channel", "occurred_at", "id"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        *COMMON_COLUMNS,
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("resource_type", sa.String(length=60), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_org_created_id", "audit_logs", ["org_id", "created_at", "id"])
    op.create_index(
        "ix_audit_logs_org_action_created_id",
        "audit_logs",
        ["org_id", "action", "created_at", "id"],
    )
    op.create_index(
        "ix_audit_logs_org_actor_created_id",
        "audit_logs",
        ["org_id", "actor_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("interactions")
    op.drop_table("members")
