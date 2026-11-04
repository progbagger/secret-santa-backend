"""Create organizer-owned groups.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "santa_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organizer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 120 AND name !~ '^[[:space:]]|[[:space:]]$'",
            name="ck_santa_groups_name_valid",
        ),
        sa.CheckConstraint("char_length(description) <= 2000", name="ck_santa_groups_description_length"),
        sa.ForeignKeyConstraint(
            ["organizer_user_id"], ["users.id"], name="fk_santa_groups_organizer", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_santa_groups"),
    )
    op.create_index("ix_santa_groups_organizer_user_id", "santa_groups", ["organizer_user_id"])


def downgrade() -> None:
    op.drop_table("santa_groups")
