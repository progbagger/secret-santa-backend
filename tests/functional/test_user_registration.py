import asyncio
from datetime import datetime

import aiohttp
import asyncpg
import pytest
from hamcrest import assert_that, contains_inanyorder, equal_to, has_entries, has_length

from tests.functional.conftest import BackendEnvironment


async def test_first_registration_returns_created_user_and_persists_it(
    running_backend: BackendEnvironment,
) -> None:
    # arrange
    login = "101001"

    # act
    async with aiohttp.ClientSession(base_url=running_backend.api_url, headers=running_backend.authorization) as client:
        response = await client.post("/api/v1/users", json={"login": login})
        await response.read()

    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        row = await connection.fetchrow(
            """
            SELECT id, login, created_at, updated_at
            FROM users
            WHERE login = $1
            """,
            login,
        )
    finally:
        await connection.close()

    # assert
    payload = await response.json()
    assert_that(response.status, equal_to(201))
    assert_that(payload, has_entries(login=login))
    assert_that(row["id"], equal_to(payload["id"]))
    assert_that(row["login"], equal_to(login))
    assert_that(row["created_at"], equal_to(datetime.fromisoformat(payload["created_at"])))
    assert_that(row["updated_at"], equal_to(datetime.fromisoformat(payload["updated_at"])))


async def test_repeated_registration_returns_same_user_and_keeps_one_row(
    running_backend: BackendEnvironment,
) -> None:
    # arrange
    login = "202002"

    # act
    async with aiohttp.ClientSession(base_url=running_backend.api_url, headers=running_backend.authorization) as client:
        first_response = await client.post("/api/v1/users", json={"login": login})
        second_response = await client.post("/api/v1/users", json={"login": login})
        await first_response.read()
        await second_response.read()

    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        row_count = await connection.fetchval(
            "SELECT count(*) FROM users WHERE login = $1",
            login,
        )
    finally:
        await connection.close()

    # assert
    assert_that(first_response.status, equal_to(201))
    assert_that(second_response.status, equal_to(200))
    assert_that((await second_response.json()), equal_to(await first_response.json()))
    assert_that(row_count, equal_to(1))


async def test_concurrent_registration_creates_one_user(
    running_backend: BackendEnvironment,
) -> None:
    # arrange
    login = "303003"

    # act
    async with aiohttp.ClientSession(base_url=running_backend.api_url, headers=running_backend.authorization) as client:
        responses = await asyncio.gather(*(client.post("/api/v1/users", json={"login": login}) for _ in range(8)))
        await asyncio.gather(*(response.read() for response in responses))

    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        rows = await connection.fetch(
            "SELECT id FROM users WHERE login = $1",
            login,
        )
    finally:
        await connection.close()

    # assert
    assert_that([response.status for response in responses], contains_inanyorder(201, *([200] * 7)))
    user_ids = {(await response.json())["id"] for response in responses}
    assert_that(user_ids, has_length(1))
    assert_that(rows, has_length(1))
    assert_that({rows[0]["id"]}, equal_to(user_ids))


async def test_different_logins_create_different_users(running_backend: BackendEnvironment) -> None:
    # arrange
    first_login, second_login = "404004", "505005"

    # act
    async with aiohttp.ClientSession(base_url=running_backend.api_url, headers=running_backend.authorization) as client:
        first_response = await client.post("/api/v1/users", json={"login": first_login})
        second_response = await client.post("/api/v1/users", json={"login": second_login})
        await first_response.read()
        await second_response.read()

    # assert
    assert_that(first_response.status, equal_to(201))
    assert_that(second_response.status, equal_to(201))
    assert_that((await first_response.json())["id"] == (await second_response.json())["id"], equal_to(False))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"login": 123},
        {"login": ""},
        {"login": "x" * 256},
        {"login": " 606006"},
        {"login": "606006 "},
    ],
)
async def test_invalid_login_returns_unprocessable_entity(
    running_backend: BackendEnvironment,
    payload: dict[str, object],
) -> None:
    # arrange
    async with aiohttp.ClientSession(base_url=running_backend.api_url, headers=running_backend.authorization) as client:
        # act
        response = await client.post("/api/v1/users", json=payload)

    # assert
    assert_that(response.status, equal_to(422))


async def test_registration_has_stable_operation_id(running_backend: BackendEnvironment) -> None:
    # arrange
    async with aiohttp.ClientSession(base_url=running_backend.api_url) as client:
        # act
        response = await client.get("/openapi.json")
        await response.read()

    # assert
    assert_that(response.status, equal_to(200))
    assert_that(
        (await response.json())["paths"]["/api/v1/users"]["post"]["operationId"],
        equal_to("register_user"),
    )
