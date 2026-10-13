import asyncpg
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

    assert_that(revision, equal_to("0001"))
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
