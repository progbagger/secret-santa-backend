import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import aiohttp
import asyncpg
import pytest
from hamcrest import assert_that, equal_to, has_entries, has_length, none

from tests.functional.conftest import BackendEnvironment


@pytest.fixture
async def client(running_backend: BackendEnvironment) -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession(
        base_url=running_backend.api_url, headers=running_backend.authorization
    ) as http_client:
        yield http_client


@pytest.fixture
async def connection(running_backend: BackendEnvironment) -> AsyncIterator[asyncpg.Connection]:
    database_connection = await asyncpg.connect(running_backend.asyncpg_url)
    try:
        yield database_connection
    finally:
        await database_connection.close()


async def _register_user(client: aiohttp.ClientSession) -> int:
    response = await client.post("/api/v1/users", json={"login": uuid4().hex})
    assert response.status == 201
    return int((await response.json())["id"])


@pytest.fixture
async def created_group(client: aiohttp.ClientSession) -> dict[str, Any]:
    organizer_id = await _register_user(client)
    response = await client.post(
        f"/api/v1/users/{organizer_id}/groups", json={"name": "Семья", "description": "Подарки"}
    )
    assert response.status == 201
    return dict(await response.json())


def _path(group: dict[str, Any]) -> str:
    return f"/api/v1/users/{group['organizer_user_id']}/groups/{group['id']}"


async def test_create_group_persists_normalized_name_and_timezone_timestamps(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection
) -> None:
    # arrange
    organizer_id = await _register_user(client)

    # act
    response = await client.post(
        f"/api/v1/users/{organizer_id}/groups", json={"name": " \tНовый год 2026\n", "description": "Семья и друзья"}
    )

    # assert
    assert response.status == 201
    group = await response.json()
    group_id = UUID(group["id"])
    assert_that(group, has_entries(organizer_user_id=organizer_id, name="Новый год 2026", description="Семья и друзья"))

    row = await connection.fetchrow("SELECT * FROM santa_groups WHERE id = $1", group_id)
    assert_that(
        dict(row),
        has_entries(id=group_id, organizer_user_id=organizer_id, name="Новый год 2026", description="Семья и друзья"),
    )
    for field in ("created_at", "updated_at"):
        timestamp = datetime.fromisoformat(group[field])
        assert timestamp.utcoffset() is not None
        assert_that(row[field], equal_to(timestamp))


async def test_get_group_returns_current_representation(
    *, client: aiohttp.ClientSession, created_group: dict[str, Any]
) -> None:
    # act
    response = await client.get(_path(created_group))

    # assert
    assert response.status == 200
    assert_that((await response.json()), equal_to(created_group))


@pytest.mark.parametrize(
    ("payload", "expected_name", "expected_description"),
    [
        ({"name": "  Друзья  "}, "Друзья", "Подарки"),
        ({"description": "  Новое описание  "}, "Семья", "  Новое описание  "),
        ({"description": None}, "Семья", None),
        ({"description": ""}, "Семья", ""),
        ({"name": "Друзья", "description": "Оба поля"}, "Друзья", "Оба поля"),
    ],
)
async def test_patch_changes_provided_fields_and_preserves_identity(
    *,
    client: aiohttp.ClientSession,
    connection: asyncpg.Connection,
    created_group: dict[str, Any],
    payload: dict[str, object],
    expected_name: str,
    expected_description: str | None,
) -> None:
    # act
    response = await client.patch(_path(created_group), json=payload)

    # assert
    assert response.status == 200
    expected = {
        "id": created_group["id"],
        "organizer_user_id": created_group["organizer_user_id"],
        "created_at": created_group["created_at"],
        "name": expected_name,
        "description": expected_description,
    }
    assert_that((await response.json()), has_entries(expected))
    assert datetime.fromisoformat((await response.json())["updated_at"]) > datetime.fromisoformat(
        created_group["updated_at"]
    )
    row = await connection.fetchrow("SELECT * FROM santa_groups WHERE id = $1", UUID(created_group["id"]))
    assert_that(dict(row), has_entries(name=expected_name, description=expected_description))
    assert_that(row["updated_at"], equal_to(datetime.fromisoformat((await response.json())["updated_at"])))


async def test_delete_removes_group_and_keeps_organizer(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_group: dict[str, Any]
) -> None:
    # act
    response = await client.delete(_path(created_group))

    # assert
    assert response.status == 204
    assert await response.read() == b""
    assert (await client.get(_path(created_group))).status == 404
    assert_that(
        await connection.fetchrow("SELECT * FROM santa_groups WHERE id = $1", UUID(created_group["id"])), none()
    )
    assert (
        await connection.fetchval("SELECT count(*) FROM users WHERE id = $1", created_group["organizer_user_id"]) == 1
    )


@pytest.mark.parametrize("payload", [{"name": "  Семья  "}, {"description": "Подарки"}, {"description": None}])
async def test_repeated_patch_preserves_group_including_updated_at(
    *,
    client: aiohttp.ClientSession,
    connection: asyncpg.Connection,
    created_group: dict[str, Any],
    payload: dict[str, object],
) -> None:
    # act
    first = await client.patch(_path(created_group), json=payload)
    second = await client.patch(_path(created_group), json=payload)

    # assert
    assert first.status == second.status == 200
    assert_that((await second.json()), equal_to(await first.json()))
    if payload.get("description", "Подарки") is not None:
        assert_that((await first.json()), equal_to(created_group))
    row = await connection.fetchrow("SELECT * FROM santa_groups WHERE id = $1", UUID(created_group["id"]))
    assert_that(row["updated_at"], equal_to(datetime.fromisoformat((await first.json())["updated_at"])))


async def test_same_name_is_allowed_for_same_and_different_organizers(client: aiohttp.ClientSession) -> None:
    # arrange
    first_owner = await _register_user(client)
    second_owner = await _register_user(client)

    # act
    responses = [
        await client.post(f"/api/v1/users/{owner}/groups", json={"name": "Одинаковое имя"})
        for owner in (first_owner, first_owner, second_owner)
    ]

    # assert
    assert_that([response.status for response in responses], equal_to([201, 201, 201]))
    assert_that({(await response.json())["id"] for response in responses}, has_length(3))
    assert_that([(await response.json())["description"] for response in responses], equal_to([None, None, None]))


async def test_unknown_organizer_cannot_create_group(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection
) -> None:
    # act
    response = await client.post("/api/v1/users/9223372036854775807/groups", json={"name": "Нет владельца"})

    # assert
    assert response.status == 404
    assert_that((await response.json()), equal_to({"detail": "Group or organizer not found"}))
    assert (
        await connection.fetchval("SELECT count(*) FROM santa_groups WHERE organizer_user_id = 9223372036854775807")
        == 0
    )


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_other_organizer_and_missing_group_have_same_not_found_response_and_do_not_mutate_group(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_group: dict[str, Any], method: str
) -> None:
    # arrange
    other_owner = await _register_user(client)
    original = await connection.fetchrow("SELECT * FROM santa_groups WHERE id = $1", UUID(created_group["id"]))
    paths = [
        f"/api/v1/users/{other_owner}/groups/{created_group['id']}",
        f"/api/v1/users/9223372036854775807/groups/{created_group['id']}",
        f"/api/v1/users/{created_group['organizer_user_id']}/groups/{uuid4()}",
    ]

    # act
    for path in paths:
        response = await client.request(method, path, **({"json": {"name": "Чужая"}} if method == "PATCH" else {}))

        # assert
        assert response.status == 404
        assert_that((await response.json()), equal_to({"detail": "Group or organizer not found"}))

    current = await connection.fetchrow("SELECT * FROM santa_groups WHERE id = $1", UUID(created_group["id"]))
    assert_that(dict(current), equal_to(dict(original)))


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": None},
        {"name": ""},
        {"name": " \t\n\u2003 "},
        {"name": "я" * 121},
        {"name": 42},
        {"name": False},
        {"name": ["Семья"]},
        {"name": "Семья", "description": "я" * 2001},
        {"name": "Семья", "description": 42},
        {"name": "Семья", "description": False},
        {"name": "Семья", "organizer_user_id": 1},
        {"name": "Семья", "id": str(uuid4())},
        {"name": "Семья", "created_at": "2026-01-01T00:00:00Z"},
        {"name": "Семья", "updated_at": "2026-01-01T00:00:00Z"},
        {"name": "Семья", "unknown": "value"},
    ],
)
async def test_invalid_group_payload_is_rejected_without_changes(
    *,
    client: aiohttp.ClientSession,
    connection: asyncpg.Connection,
    created_group: dict[str, Any],
    method: str,
    payload: dict[str, object],
) -> None:
    # arrange
    owner_id = created_group["organizer_user_id"]
    path = f"/api/v1/users/{owner_id}/groups" if method == "POST" else _path(created_group)

    # act
    response = await client.request(method, path, json=payload)

    # assert
    assert response.status == 422
    fetched = await client.get(_path(created_group))
    assert_that((await fetched.json()), equal_to(created_group))
    assert await connection.fetchval("SELECT count(*) FROM santa_groups WHERE organizer_user_id = $1", owner_id) == 1


@pytest.mark.parametrize("description", ["", None, "я" * 2000])
async def test_boundary_lengths_and_nullable_description_are_accepted(
    *, client: aiohttp.ClientSession, description: str | None
) -> None:
    # arrange
    owner_id = await _register_user(client)
    name = "я" * 120

    # act
    response = await client.post(
        f"/api/v1/users/{owner_id}/groups", json={"name": f" {name} ", "description": description}
    )

    # assert
    assert response.status == 201
    assert_that((await response.json()), has_entries(name=name, description=description))
    patched = await client.patch(_path(await response.json()), json={"name": name, "description": description})
    assert patched.status == 200
    assert_that((await patched.json()), equal_to(await response.json()))


async def test_concurrent_partial_patches_keep_both_fields(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_group: dict[str, Any]
) -> None:
    # act
    responses = await asyncio.gather(
        client.patch(_path(created_group), json={"name": "Новое имя"}),
        client.patch(_path(created_group), json={"description": "Новое описание"}),
    )

    # assert
    assert_that([response.status for response in responses], equal_to([200, 200]))
    fetched = await client.get(_path(created_group))
    assert_that((await fetched.json()), has_entries(name="Новое имя", description="Новое описание"))
    row = await connection.fetchrow(
        "SELECT name, description FROM santa_groups WHERE id = $1", UUID(created_group["id"])
    )
    assert_that(dict(row), equal_to({"name": "Новое имя", "description": "Новое описание"}))


async def test_repeated_delete_does_not_change_another_group(
    *, client: aiohttp.ClientSession, created_group: dict[str, Any]
) -> None:
    # arrange
    sibling = await client.post(f"/api/v1/users/{created_group['organizer_user_id']}/groups", json={"name": "Соседняя"})
    assert sibling.status == 201

    # act
    first = await client.delete(_path(created_group))
    repeated = await client.delete(_path(created_group))
    fetched = await client.get(_path(await sibling.json()))

    # assert
    assert first.status == 204
    assert repeated.status == 404
    assert fetched.status == 200
    assert_that((await fetched.json()), equal_to(await sibling.json()))


async def test_group_openapi_has_stable_operations_and_response_schemas(client: aiohttp.ClientSession) -> None:
    # act
    response = await client.get("/openapi.json")

    # assert
    assert response.status == 200
    schema = await response.json()
    collection = "/api/v1/users/{organizer_user_id}/groups"
    for method, path, operation_id, status_code in [
        ("post", collection, "create_group", "201"),
        ("get", collection + "/{group_id}", "get_group", "200"),
        ("patch", collection + "/{group_id}", "update_group", "200"),
        ("delete", collection + "/{group_id}", "delete_group", "204"),
    ]:
        operation = schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert "групп" in operation["description"]
        assert "404" in operation["responses"]
        assert "422" in operation["responses"]
        successful_response = operation["responses"][status_code]
        if method == "delete":
            assert "content" not in successful_response
        else:
            assert_that(
                successful_response["content"]["application/json"]["schema"],
                equal_to({"$ref": "#/components/schemas/GroupResponse"}),
            )

    models = schema["components"]["schemas"]
    assert_that(
        set(models["GroupResponse"]["required"]),
        equal_to({"id", "organizer_user_id", "name", "description", "created_at", "updated_at"}),
    )
    assert models["GroupUpdateRequest"]["additionalProperties"] is False
    assert models["GroupUpdateRequest"]["properties"]["name"]["type"] == "string"
