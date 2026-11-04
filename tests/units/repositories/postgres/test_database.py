import asyncio

import pytest
from hamcrest import assert_that, same_instance

from secret_santa_backend.repositories.postgres import (
    PostgresDatabase,
    PostgresDatabaseUnavailableError,
    PostgresSessionUnavailableError,
    get,
    get_database,
    transaction,
    use_database,
)


def test_get_raises_diagnostic_error_outside_transaction() -> None:
    with pytest.raises(PostgresSessionUnavailableError, match="only available inside transaction"):
        get()


async def test_transaction_raises_diagnostic_error_without_database_context() -> None:
    with pytest.raises(PostgresDatabaseUnavailableError, match="only available inside use_database"):
        async with transaction():
            pytest.fail("transaction must not start without a database context")


async def test_nested_database_context_restores_previous_instance() -> None:
    first = PostgresDatabase("postgresql+asyncpg://localhost/first")
    second = PostgresDatabase("postgresql+asyncpg://localhost/second")
    try:
        with use_database(first):
            assert_that(get_database(), same_instance(first))
            with use_database(second):
                assert_that(get_database(), same_instance(second))
            assert_that(get_database(), same_instance(first))

        with pytest.raises(PostgresDatabaseUnavailableError):
            get_database()
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_database_context_is_reset_after_exception_or_cancellation(error: type[BaseException]) -> None:
    database = PostgresDatabase("postgresql+asyncpg://localhost/test")
    try:
        with pytest.raises(error), use_database(database):
            raise error

        with pytest.raises(PostgresDatabaseUnavailableError):
            get_database()
    finally:
        await database.close()


async def test_concurrent_tasks_keep_their_own_database_context() -> None:
    first = PostgresDatabase("postgresql+asyncpg://localhost/first")
    second = PostgresDatabase("postgresql+asyncpg://localhost/second")
    ready = asyncio.Event()

    async def read_database() -> PostgresDatabase:
        await ready.wait()
        return get_database()

    try:
        async with asyncio.TaskGroup() as tasks:
            with use_database(first):
                first_task = tasks.create_task(read_database())
            with use_database(second):
                second_task = tasks.create_task(read_database())
            ready.set()

        assert_that(first_task.result(), same_instance(first))
        assert_that(second_task.result(), same_instance(second))
        with pytest.raises(PostgresDatabaseUnavailableError):
            get_database()
    finally:
        await first.close()
        await second.close()
