"""Replace groups with games and add schedules and participants.

Revision ID: 0003
Revises: 0002

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rename_game_table(*, old: str, new: str) -> None:
    op.rename_table(old, new)
    for before, after in (
        (f"pk_{old}", f"pk_{new}"),
        (f"fk_{old}_organizer", f"fk_{new}_organizer"),
        (f"ck_{old}_name_valid", f"ck_{new}_name_valid"),
        (f"ck_{old}_description_length", f"ck_{new}_description_length"),
    ):
        op.execute(f"ALTER TABLE {new} RENAME CONSTRAINT {before} TO {after}")
    op.execute(f"ALTER INDEX ix_{old}_organizer_user_id RENAME TO ix_{new}_organizer_user_id")


def upgrade() -> None:
    _rename_game_table(old="santa_groups", new="santa_games")
    op.add_column("santa_games", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("santa_games", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_santa_games_schedule", "santa_games", "ends_at > starts_at")

    op.create_table(
        "santa_game_participants",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_id"], ["santa_games.id"], name="fk_santa_game_participants_game", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_santa_game_participants_user", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("game_id", "user_id", name="pk_santa_game_participants"),
    )
    op.create_index("ix_santa_game_participants_user_id", "santa_game_participants", ["user_id"])


def downgrade() -> None:
    op.drop_table("santa_game_participants")
    op.drop_constraint("ck_santa_games_schedule", "santa_games", type_="check")
    op.drop_column("santa_games", "ends_at")
    op.drop_column("santa_games", "starts_at")
    _rename_game_table(old="santa_games", new="santa_groups")
