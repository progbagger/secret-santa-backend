import asyncio
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from hamcrest import assert_that, equal_to, has_entries, has_items

from tests.functional.conftest import BackendEnvironment


async def test_alembic_upgrade_creates_users_schema(running_backend: BackendEnvironment) -> None:
    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
        columns = await connection.fetch(
            """
            SELECT column_name, data_type, is_nullable, is_identity
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users'
            ORDER BY ordinal_position
            """
        )
        constraints = await connection.fetch(
            """
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_schema = 'public' AND table_name = 'users'
            """
        )
    finally:
        await connection.close()

    assert_that(revision, equal_to("0003"))
    assert_that(
        [dict(column) for column in columns],
        equal_to(
            [
                {"column_name": "id", "data_type": "bigint", "is_nullable": "NO", "is_identity": "YES"},
                {
                    "column_name": "login",
                    "data_type": "character varying",
                    "is_nullable": "NO",
                    "is_identity": "NO",
                },
                {
                    "column_name": "created_at",
                    "data_type": "timestamp with time zone",
                    "is_nullable": "NO",
                    "is_identity": "NO",
                },
                {
                    "column_name": "updated_at",
                    "data_type": "timestamp with time zone",
                    "is_nullable": "NO",
                    "is_identity": "NO",
                },
            ]
        ),
    )
    assert_that(
        [row["constraint_name"] for row in constraints],
        has_items(
            "pk_users",
            "uq_users_login",
            "ck_users_login_valid",
        ),
    )


async def test_users_timestamps_have_database_defaults(running_backend: BackendEnvironment) -> None:
    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        defaults = await connection.fetchrow(
            """
            SELECT
                column_default AS created_at_default,
                (
                    SELECT column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'updated_at'
                ) AS updated_at_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'created_at'
            """
        )
    finally:
        await connection.close()

    assert_that(dict(defaults), has_entries(created_at_default="now()", updated_at_default="now()"))


@pytest.fixture
def legacy_database(tmp_path: Path) -> Iterator[BackendEnvironment]:
    environment = BackendEnvironment(tmp_path)
    try:
        environment.start_postgres()
        environment.migrate("0001")
        yield environment
    finally:
        environment.close()


async def test_group_migration_preserves_existing_user_and_creates_constrained_schema(
    legacy_database: BackendEnvironment,
) -> None:
    connection = await asyncpg.connect(legacy_database.asyncpg_url)
    try:
        original = await connection.fetchrow("INSERT INTO users (login) VALUES ('before-group-migration') RETURNING *")

        await asyncio.to_thread(legacy_database.migrate, "0002")

        current = await connection.fetchrow("SELECT * FROM users WHERE id = $1", original["id"])
        assert_that(dict(current), equal_to(dict(original)))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0002"
        columns = await connection.fetch(
            """
            SELECT column_name, data_type, is_nullable, character_maximum_length, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'santa_groups'
            ORDER BY ordinal_position
            """
        )
        assert_that(
            [(row["column_name"], row["data_type"], row["is_nullable"]) for row in columns],
            equal_to(
                [
                    ("id", "uuid", "NO"),
                    ("organizer_user_id", "bigint", "NO"),
                    ("name", "character varying", "NO"),
                    ("description", "text", "YES"),
                    ("created_at", "timestamp with time zone", "NO"),
                    ("updated_at", "timestamp with time zone", "NO"),
                ]
            ),
        )
        assert columns[2]["character_maximum_length"] == 120
        assert columns[4]["column_default"] == columns[5]["column_default"] == "now()"
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE tablename = 'santa_groups' AND indexname = $1",
                "ix_santa_groups_organizer_user_id",
            )
            == 1
        )

        group_id = uuid4()
        await connection.execute(
            "INSERT INTO santa_groups (id, organizer_user_id, name) VALUES ($1, $2, 'Семья')",
            group_id,
            original["id"],
        )
        with pytest.raises(asyncpg.RestrictViolationError):
            await connection.execute("DELETE FROM users WHERE id = $1", original["id"])
        assert await connection.fetchval("SELECT count(*) FROM users WHERE id = $1", original["id"]) == 1
        assert await connection.fetchval("SELECT count(*) FROM santa_groups WHERE id = $1", group_id) == 1
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                "INSERT INTO santa_groups (id, organizer_user_id, name) VALUES ($1, $2, 'Нет владельца')",
                uuid4(),
                9223372036854775807,
            )
    finally:
        await connection.close()


@pytest.mark.parametrize(
    ("name", "description", "error"),
    [
        ("", None, asyncpg.CheckViolationError),
        ("   ", None, asyncpg.CheckViolationError),
        ("\t\n", None, asyncpg.CheckViolationError),
        (" имя ", None, asyncpg.CheckViolationError),
        ("\tимя\n", None, asyncpg.CheckViolationError),
        ("я" * 121, None, asyncpg.StringDataRightTruncationError),
        ("Имя", "я" * 2001, asyncpg.CheckViolationError),
        (None, None, asyncpg.NotNullViolationError),
    ],
)
async def test_game_database_rejects_invalid_values(
    *, running_backend: BackendEnvironment, name: str | None, description: str | None, error: type[Exception]
) -> None:
    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        owner_id = await connection.fetchval("INSERT INTO users (login) VALUES ($1) RETURNING id", uuid4().hex)
        with pytest.raises(error):
            await connection.execute(
                "INSERT INTO santa_games (id, organizer_user_id, name, description) VALUES ($1, $2, $3, $4)",
                uuid4(),
                owner_id,
                name,
                description,
            )
        assert await connection.fetchval("SELECT count(*) FROM santa_games WHERE organizer_user_id = $1", owner_id) == 0
    finally:
        await connection.close()


async def test_game_migration_preserves_groups_and_users_through_upgrade_and_downgrade(
    legacy_database: BackendEnvironment,
) -> None:
    # arrange
    await asyncio.to_thread(legacy_database.migrate, "0002")
    connection = await asyncpg.connect(legacy_database.asyncpg_url)
    try:
        users = await connection.fetch("INSERT INTO users (login) VALUES ('first'), ('second') RETURNING *")
        groups = [
            await connection.fetchrow(
                """
                INSERT INTO santa_groups (id, organizer_user_id, name, description, created_at, updated_at)
                VALUES ($1, $2, $3, $4, '2025-11-01T00:00:00+07:00', '2025-11-15T00:00:00+07:00')
                RETURNING *
                """,
                uuid4(),
                organizer,
                name,
                description,
            )
            for organizer, name, description in (
                (users[0]["id"], "Семья", "Подарки"),
                (users[0]["id"], "Друзья", None),
                (users[1]["id"], "Семья", ""),
            )
        ]
        expected_groups = {row["id"]: dict(row) for row in groups}
        expected_users = {row["id"]: dict(row) for row in users}

        # act
        await asyncio.to_thread(legacy_database.migrate)

        # assert
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0003"
        assert await connection.fetchval("SELECT to_regclass('santa_groups')") is None
        assert_that(
            {row["id"]: dict(row) for row in await connection.fetch("SELECT * FROM santa_games")},
            equal_to({key: dict(row, starts_at=None, ends_at=None) for key, row in expected_groups.items()}),
        )
        assert_that(
            {row["id"]: dict(row) for row in await connection.fetch("SELECT * FROM users")}, equal_to(expected_users)
        )
        assert await connection.fetchval("SELECT count(*) FROM santa_game_participants") == 0
        assert await connection.fetchval("SELECT to_regclass('ix_santa_games_organizer_user_id')") is not None

        # arrange
        game_id = groups[0]["id"]
        await connection.execute("UPDATE santa_games SET starts_at = now() WHERE id = $1", game_id)
        await connection.execute(
            "INSERT INTO santa_game_participants (game_id, user_id) VALUES ($1, $2)", game_id, users[1]["id"]
        )

        # act
        await asyncio.to_thread(legacy_database.migrate, "0002", direction="downgrade")

        # assert
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0002"
        assert await connection.fetchval("SELECT to_regclass('santa_games')") is None
        assert await connection.fetchval("SELECT to_regclass('santa_game_participants')") is None
        assert_that(
            {row["id"]: dict(row) for row in await connection.fetch("SELECT * FROM santa_groups")},
            equal_to(expected_groups),
        )
        assert_that(
            {row["id"]: dict(row) for row in await connection.fetch("SELECT * FROM users")}, equal_to(expected_users)
        )
    finally:
        await connection.close()


async def test_game_schema_enforces_schedule_and_participant_integrity(running_backend: BackendEnvironment) -> None:
    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        # arrange
        organizer = await connection.fetchval("INSERT INTO users (login) VALUES ($1) RETURNING id", uuid4().hex)
        participant = await connection.fetchval("INSERT INTO users (login) VALUES ($1) RETURNING id", uuid4().hex)
        game_id = uuid4()
        start = datetime.fromisoformat("2026-12-20T12:00:00+07:00")
        same_instant = datetime.fromisoformat("2026-12-20T05:00:00Z")
        add_participant = "INSERT INTO santa_game_participants (game_id, user_id) VALUES ($1, $2)"
        await connection.execute(
            "INSERT INTO santa_games (id, organizer_user_id, name, starts_at) VALUES ($1, $2, 'Семья', $3)",
            game_id,
            organizer,
            start,
        )
        await connection.execute(add_participant, game_id, participant)

        # act / assert
        with pytest.raises(asyncpg.CheckViolationError, match="ck_santa_games_schedule"):
            await connection.execute("UPDATE santa_games SET ends_at = $1 WHERE id = $2", same_instant, game_id)
        with pytest.raises(asyncpg.UniqueViolationError, match="pk_santa_game_participants"):
            await connection.execute(add_participant, game_id, participant)
        with pytest.raises(asyncpg.ForeignKeyViolationError, match="fk_santa_game_participants_game"):
            await connection.execute(add_participant, uuid4(), participant)
        with pytest.raises(asyncpg.ForeignKeyViolationError, match="fk_santa_game_participants_user"):
            await connection.execute(add_participant, game_id, 9223372036854775807)
        with pytest.raises(asyncpg.RestrictViolationError, match="fk_santa_game_participants_user"):
            await connection.execute("DELETE FROM users WHERE id = $1", participant)
        with pytest.raises(asyncpg.RestrictViolationError, match="fk_santa_games_organizer"):
            await connection.execute("DELETE FROM users WHERE id = $1", organizer)
    finally:
        await connection.close()
