import asyncio
from datetime import datetime

import asyncpg
import httpx
import pytest
from hamcrest import assert_that, contains_inanyorder, equal_to, has_entries, has_length

from tests.functional.conftest import BackendEnvironment


async def test_first_registration_returns_created_user_and_persists_it(
    running_backend: BackendEnvironment,
) -> None:
    login = "101001"

    async with httpx.AsyncClient(base_url=running_backend.api_url) as client:
        response = await client.post("/api/v1/users", json={"login": login})

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

    payload = response.json()
    assert_that(response.status_code, equal_to(201))
    assert_that(payload, has_entries(login=login))
    assert_that(row["id"], equal_to(payload["id"]))
    assert_that(row["login"], equal_to(login))
    assert_that(row["created_at"], equal_to(datetime.fromisoformat(payload["created_at"])))
    assert_that(row["updated_at"], equal_to(datetime.fromisoformat(payload["updated_at"])))


async def test_repeated_registration_returns_same_user_and_keeps_one_row(
    running_backend: BackendEnvironment,
) -> None:
    login = "202002"

    async with httpx.AsyncClient(base_url=running_backend.api_url) as client:
        first_response = await client.post("/api/v1/users", json={"login": login})
        second_response = await client.post("/api/v1/users", json={"login": login})

    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        row_count = await connection.fetchval(
            "SELECT count(*) FROM users WHERE login = $1",
            login,
        )
    finally:
        await connection.close()

    assert_that(first_response.status_code, equal_to(201))
    assert_that(second_response.status_code, equal_to(200))
    assert_that(second_response.json(), equal_to(first_response.json()))
    assert_that(row_count, equal_to(1))


async def test_concurrent_registration_creates_one_user(
    running_backend: BackendEnvironment,
) -> None:
    login = "303003"

    async with httpx.AsyncClient(base_url=running_backend.api_url) as client:
        responses = await asyncio.gather(*(client.post("/api/v1/users", json={"login": login}) for _ in range(8)))

    connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        rows = await connection.fetch(
            "SELECT id FROM users WHERE login = $1",
            login,
        )
    finally:
        await connection.close()

    assert_that([response.status_code for response in responses], contains_inanyorder(201, *([200] * 7)))
    assert_that({response.json()["id"] for response in responses}, has_length(1))
    assert_that(rows, has_length(1))
    assert_that(rows[0]["id"], equal_to(responses[0].json()["id"]))


async def test_different_logins_create_different_users(running_backend: BackendEnvironment) -> None:
    async with httpx.AsyncClient(base_url=running_backend.api_url) as client:
        first_response = await client.post("/api/v1/users", json={"login": "404004"})
        second_response = await client.post("/api/v1/users", json={"login": "505005"})

    assert_that(first_response.status_code, equal_to(201))
    assert_that(second_response.status_code, equal_to(201))
    assert_that(first_response.json()["id"] == second_response.json()["id"], equal_to(False))


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
    async with httpx.AsyncClient(base_url=running_backend.api_url) as client:
        response = await client.post("/api/v1/users", json=payload)

    assert_that(response.status_code, equal_to(422))


async def test_registration_has_stable_operation_id(running_backend: BackendEnvironment) -> None:
    async with httpx.AsyncClient(base_url=running_backend.api_url) as client:
        response = await client.get("/openapi.json")

    assert_that(response.status_code, equal_to(200))
    assert_that(
        response.json()["paths"]["/api/v1/users"]["post"]["operationId"],
        equal_to("register_user"),
    )
