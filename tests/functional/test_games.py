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
async def created_game(client: aiohttp.ClientSession) -> dict[str, Any]:
    organizer_id = await _register_user(client)
    response = await client.post(
        f"/api/v1/users/{organizer_id}/games", json={"name": "Семья", "description": "Подарки"}
    )
    assert response.status == 201
    return dict(await response.json())


def _path(game: dict[str, Any]) -> str:
    return f"/api/v1/users/{game['organizer_user_id']}/games/{game['id']}"


async def test_create_game_persists_normalized_name_and_timezone_timestamps(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection
) -> None:
    # arrange
    organizer_id = await _register_user(client)

    # act
    response = await client.post(
        f"/api/v1/users/{organizer_id}/games", json={"name": " \tНовый год 2026\n", "description": "Семья и друзья"}
    )

    # assert
    assert response.status == 201
    game = await response.json()
    game_id = UUID(game["id"])
    assert_that(
        game,
        has_entries(
            organizer_user_id=organizer_id,
            name="Новый год 2026",
            description="Семья и друзья",
            starts_at=None,
            ends_at=None,
        ),
    )

    row = await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", game_id)
    assert_that(
        dict(row),
        has_entries(id=game_id, organizer_user_id=organizer_id, name="Новый год 2026", description="Семья и друзья"),
    )
    for field in ("created_at", "updated_at"):
        timestamp = datetime.fromisoformat(game[field])
        assert timestamp.utcoffset() is not None
        assert_that(row[field], equal_to(timestamp))


async def test_get_game_returns_current_representation(
    *, client: aiohttp.ClientSession, created_game: dict[str, Any]
) -> None:
    # act
    response = await client.get(_path(created_game))

    # assert
    assert response.status == 200
    assert_that((await response.json()), equal_to(created_game))


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
    created_game: dict[str, Any],
    payload: dict[str, object],
    expected_name: str,
    expected_description: str | None,
) -> None:
    # act
    response = await client.patch(_path(created_game), json=payload)

    # assert
    assert response.status == 200
    expected = {
        "id": created_game["id"],
        "organizer_user_id": created_game["organizer_user_id"],
        "created_at": created_game["created_at"],
        "name": expected_name,
        "description": expected_description,
    }
    assert_that((await response.json()), has_entries(expected))
    assert datetime.fromisoformat((await response.json())["updated_at"]) > datetime.fromisoformat(
        created_game["updated_at"]
    )
    row = await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", UUID(created_game["id"]))
    assert_that(dict(row), has_entries(name=expected_name, description=expected_description))
    assert_that(row["updated_at"], equal_to(datetime.fromisoformat((await response.json())["updated_at"])))


async def test_delete_removes_game_and_keeps_organizer(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any]
) -> None:
    # act
    response = await client.delete(_path(created_game))

    # assert
    assert response.status == 204
    assert await response.read() == b""
    assert (await client.get(_path(created_game))).status == 404
    assert_that(await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", UUID(created_game["id"])), none())
    assert await connection.fetchval("SELECT count(*) FROM users WHERE id = $1", created_game["organizer_user_id"]) == 1


@pytest.mark.parametrize("payload", [{"name": "  Семья  "}, {"description": "Подарки"}, {"description": None}])
async def test_repeated_patch_preserves_game_including_updated_at(
    *,
    client: aiohttp.ClientSession,
    connection: asyncpg.Connection,
    created_game: dict[str, Any],
    payload: dict[str, object],
) -> None:
    # act
    first = await client.patch(_path(created_game), json=payload)
    second = await client.patch(_path(created_game), json=payload)

    # assert
    assert first.status == second.status == 200
    assert_that((await second.json()), equal_to(await first.json()))
    if payload.get("description", "Подарки") is not None:
        assert_that((await first.json()), equal_to(created_game))
    row = await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", UUID(created_game["id"]))
    assert_that(row["updated_at"], equal_to(datetime.fromisoformat((await first.json())["updated_at"])))


async def test_same_name_is_allowed_for_same_and_different_organizers(client: aiohttp.ClientSession) -> None:
    # arrange
    first_owner = await _register_user(client)
    second_owner = await _register_user(client)

    # act
    responses = [
        await client.post(f"/api/v1/users/{owner}/games", json={"name": "Одинаковое имя"})
        for owner in (first_owner, first_owner, second_owner)
    ]

    # assert
    assert_that([response.status for response in responses], equal_to([201, 201, 201]))
    assert_that({(await response.json())["id"] for response in responses}, has_length(3))
    assert_that([(await response.json())["description"] for response in responses], equal_to([None, None, None]))


async def test_unknown_organizer_cannot_create_game(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection
) -> None:
    # act
    response = await client.post("/api/v1/users/9223372036854775807/games", json={"name": "Нет владельца"})

    # assert
    assert response.status == 404
    assert_that((await response.json()), equal_to({"detail": "Game or organizer not found"}))
    assert (
        await connection.fetchval("SELECT count(*) FROM santa_games WHERE organizer_user_id = 9223372036854775807") == 0
    )


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_other_organizer_and_missing_game_have_same_not_found_response_and_do_not_mutate_game(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any], method: str
) -> None:
    # arrange
    other_owner = await _register_user(client)
    original = await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", UUID(created_game["id"]))
    paths = [
        f"/api/v1/users/{other_owner}/games/{created_game['id']}",
        f"/api/v1/users/9223372036854775807/games/{created_game['id']}",
        f"/api/v1/users/{created_game['organizer_user_id']}/games/{uuid4()}",
    ]

    # act
    for path in paths:
        response = await client.request(method, path, **({"json": {"name": "Чужая"}} if method == "PATCH" else {}))

        # assert
        assert response.status == 404
        assert_that((await response.json()), equal_to({"detail": "Game or organizer not found"}))

    current = await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", UUID(created_game["id"]))
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
async def test_invalid_game_payload_is_rejected_without_changes(
    *,
    client: aiohttp.ClientSession,
    connection: asyncpg.Connection,
    created_game: dict[str, Any],
    method: str,
    payload: dict[str, object],
) -> None:
    # arrange
    owner_id = created_game["organizer_user_id"]
    path = f"/api/v1/users/{owner_id}/games" if method == "POST" else _path(created_game)

    # act
    response = await client.request(method, path, json=payload)

    # assert
    assert response.status == 422
    fetched = await client.get(_path(created_game))
    assert_that((await fetched.json()), equal_to(created_game))
    assert await connection.fetchval("SELECT count(*) FROM santa_games WHERE organizer_user_id = $1", owner_id) == 1


@pytest.mark.parametrize("description", ["", None, "я" * 2000])
async def test_boundary_lengths_and_nullable_description_are_accepted(
    *, client: aiohttp.ClientSession, description: str | None
) -> None:
    # arrange
    owner_id = await _register_user(client)
    name = "я" * 120

    # act
    response = await client.post(
        f"/api/v1/users/{owner_id}/games", json={"name": f" {name} ", "description": description}
    )

    # assert
    assert response.status == 201
    assert_that((await response.json()), has_entries(name=name, description=description))
    patched = await client.patch(_path(await response.json()), json={"name": name, "description": description})
    assert patched.status == 200
    assert_that((await patched.json()), equal_to(await response.json()))


async def test_concurrent_partial_patches_keep_both_fields(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any]
) -> None:
    # act
    responses = await asyncio.gather(
        client.patch(_path(created_game), json={"name": "Новое имя"}),
        client.patch(_path(created_game), json={"description": "Новое описание"}),
    )

    # assert
    assert_that([response.status for response in responses], equal_to([200, 200]))
    fetched = await client.get(_path(created_game))
    assert_that((await fetched.json()), has_entries(name="Новое имя", description="Новое описание"))
    row = await connection.fetchrow("SELECT name, description FROM santa_games WHERE id = $1", UUID(created_game["id"]))
    assert_that(dict(row), equal_to({"name": "Новое имя", "description": "Новое описание"}))


async def test_repeated_delete_does_not_change_another_game(
    *, client: aiohttp.ClientSession, created_game: dict[str, Any]
) -> None:
    # arrange
    sibling = await client.post(f"/api/v1/users/{created_game['organizer_user_id']}/games", json={"name": "Соседняя"})
    assert sibling.status == 201

    # act
    first = await client.delete(_path(created_game))
    repeated = await client.delete(_path(created_game))
    fetched = await client.get(_path(await sibling.json()))

    # assert
    assert first.status == 204
    assert repeated.status == 404
    assert fetched.status == 200
    assert_that((await fetched.json()), equal_to(await sibling.json()))


async def test_list_games_returns_only_games_of_the_organizer(client: aiohttp.ClientSession) -> None:
    # arrange
    owner = await _register_user(client)
    other = await _register_user(client)
    empty_owner = await _register_user(client)
    owner_path = f"/api/v1/users/{owner}/games"
    other_path = f"/api/v1/users/{other}/games"
    first = await client.post(owner_path, json={"name": "Семья"})
    second = await client.post(owner_path, json={"name": "Друзья"})
    unrelated = await client.post(other_path, json={"name": "Семья"})
    assert first.status == second.status == unrelated.status == 201

    # act
    own_games = await client.get(owner_path)
    other_games = await client.get(other_path)
    empty_games = await client.get(f"/api/v1/users/{empty_owner}/games")
    unknown_owner = await client.get("/api/v1/users/9223372036854775807/games")

    # assert
    assert own_games.status == other_games.status == empty_games.status == 200
    assert_that(await own_games.json(), equal_to([await first.json(), await second.json()]))
    assert_that(await other_games.json(), equal_to([await unrelated.json()]))
    assert_that(await empty_games.json(), equal_to([]))
    assert unknown_owner.status == 404
    assert_that(await unknown_owner.json(), equal_to({"detail": "Game or organizer not found"}))


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
@pytest.mark.parametrize("value", [42, "1767225600", "2026-12-20", "2026-12-20T12:00:00"])
async def test_game_rejects_schedule_without_timezone_aware_datetime(
    *, client: aiohttp.ClientSession, created_game: dict[str, Any], field: str, value: object
) -> None:
    # act
    response = await client.patch(_path(created_game), json={field: value})

    # assert
    assert response.status == 422
    assert_that(await (await client.get(_path(created_game))).json(), equal_to(created_game))


@pytest.mark.parametrize("ends_at", ["2026-12-20T05:00:00Z", "2026-12-20T04:59:00Z"])
async def test_create_game_rejects_equal_or_reversed_schedule(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, ends_at: str
) -> None:
    # arrange
    owner = await _register_user(client)

    # act
    response = await client.post(
        f"/api/v1/users/{owner}/games",
        json={"name": "Семья", "starts_at": "2026-12-20T12:00:00+07:00", "ends_at": ends_at},
    )

    # assert
    assert response.status == 422
    assert await connection.fetchval("SELECT count(*) FROM santa_games WHERE organizer_user_id = $1", owner) == 0


async def test_patch_validates_final_schedule_and_can_clear_each_date(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection
) -> None:
    # arrange
    owner = await _register_user(client)
    start = "2026-12-20T12:00:00+07:00"
    end = "2026-12-20T06:00:00Z"
    created = await client.post(
        f"/api/v1/users/{owner}/games", json={"name": "Семья", "starts_at": start, "ends_at": end}
    )
    assert created.status == 201
    original = await created.json()
    path = _path(original)

    # act
    rejected = await client.patch(path, json={"name": "Новое имя", "ends_at": "2026-12-20T05:00:00Z"})
    unchanged = await client.get(path)
    row = await connection.fetchrow("SELECT * FROM santa_games WHERE id = $1", UUID(original["id"]))
    same_instant = await client.patch(path, json={"starts_at": "2026-12-20T05:00:00Z"})
    later = await client.patch(path, json={"ends_at": "2026-12-20T09:00:00Z"})
    only_end = await client.patch(path, json={"starts_at": None})
    only_start = await client.patch(path, json={"starts_at": start, "ends_at": None})

    # assert
    assert rejected.status == 422
    assert unchanged.status == same_instant.status == later.status == only_end.status == only_start.status == 200
    assert_that(await unchanged.json(), equal_to(original))
    assert_that(await same_instant.json(), equal_to(original))
    assert_that(
        dict(row),
        has_entries(
            name="Семья",
            starts_at=datetime.fromisoformat(start),
            ends_at=datetime.fromisoformat(end),
            updated_at=datetime.fromisoformat(original["updated_at"]),
        ),
    )
    assert_that(await later.json(), has_entries(starts_at="2026-12-20T05:00:00Z", ends_at="2026-12-20T09:00:00Z"))
    assert_that(await only_end.json(), has_entries(starts_at=None, ends_at="2026-12-20T09:00:00Z"))
    assert_that(await only_start.json(), has_entries(starts_at="2026-12-20T05:00:00Z", ends_at=None))


async def test_concurrent_patches_cannot_persist_an_invalid_schedule(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any]
) -> None:
    # arrange
    path = _path(created_game)
    start = "2026-12-20T18:00:00Z"
    end = "2026-12-20T12:00:00Z"

    # act
    starts, ends = await asyncio.gather(
        client.patch(path, json={"starts_at": start}),
        client.patch(path, json={"ends_at": end}),
    )

    # assert
    assert_that(sorted([starts.status, ends.status]), equal_to([200, 422]))
    row = await connection.fetchrow(
        "SELECT starts_at, ends_at FROM santa_games WHERE id = $1", UUID(created_game["id"])
    )
    expected = (
        {"starts_at": datetime.fromisoformat(start), "ends_at": None}
        if starts.status == 200
        else {"starts_at": None, "ends_at": datetime.fromisoformat(end)}
    )
    assert_that(dict(row), equal_to(expected))


async def test_game_participants_are_independent_and_organizer_can_join(
    *, client: aiohttp.ClientSession, created_game: dict[str, Any]
) -> None:
    # arrange
    first = _path(created_game) + "/participants"
    owner = created_game["organizer_user_id"]
    sibling = await client.post(f"/api/v1/users/{owner}/games", json={"name": "Соседняя"})
    assert sibling.status == 201
    second = _path(await sibling.json()) + "/participants"
    participant = await _register_user(client)

    # act
    added = [
        await client.put(path) for path in (f"{first}/{participant}", f"{first}/{owner}", f"{second}/{participant}")
    ]
    first_roster = await client.get(first)
    removed = await client.delete(f"{first}/{participant}")

    # assert
    assert_that([response.status for response in added], equal_to([204, 204, 204]))
    assert_that(await first_roster.json(), equal_to([{"user_id": owner}, {"user_id": participant}]))
    assert removed.status == 204
    assert await removed.read() == b""
    assert_that(await (await client.get(first)).json(), equal_to([{"user_id": owner}]))
    assert_that(await (await client.get(second)).json(), equal_to([{"user_id": participant}]))


async def test_concurrent_and_repeated_add_keep_one_participant(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any]
) -> None:
    # arrange
    participant = await _register_user(client)
    collection = _path(created_game) + "/participants"
    path = f"{collection}/{participant}"
    game_id = UUID(created_game["id"])

    # act
    first, concurrent = await asyncio.gather(client.put(path), client.put(path))
    repeated = await client.put(path)

    # assert
    assert first.status == concurrent.status == repeated.status == 204
    assert await repeated.read() == b""
    response = await client.get(collection)
    assert response.status == 200
    assert_that(await response.json(), equal_to([{"user_id": participant}]))
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM santa_game_participants WHERE game_id = $1 AND user_id = $2", game_id, participant
        )
        == 1
    )

    # act
    removed = await client.delete(path)
    repeated_remove = await client.delete(path)

    # assert
    assert removed.status == repeated_remove.status == 204
    assert_that(await (await client.get(collection)).json(), equal_to([]))


async def test_unknown_user_cannot_be_added_to_game(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any]
) -> None:
    # act
    response = await client.put(_path(created_game) + "/participants/9223372036854775807")

    # assert
    assert response.status == 404
    assert_that(await response.json(), equal_to({"detail": "User not found"}))
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM santa_game_participants WHERE game_id = $1", UUID(created_game["id"])
        )
        == 0
    )


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
async def test_participant_routes_return_same_404_for_other_organizer_and_missing_game(
    *, client: aiohttp.ClientSession, created_game: dict[str, Any], method: str
) -> None:
    # arrange
    other = await _register_user(client)
    owner = created_game["organizer_user_id"]
    collection = _path(created_game) + "/participants"
    assert (await client.put(f"{collection}/{owner}")).status == 204
    suffix = "" if method == "GET" else f"/{owner if method == 'DELETE' else 9223372036854775807}"
    paths = [
        f"/api/v1/users/{other}/games/{created_game['id']}/participants{suffix}",
        f"/api/v1/users/{owner}/games/{uuid4()}/participants{suffix}",
    ]

    # act
    responses = [await client.request(method, path) for path in paths]

    # assert
    for response in responses:
        assert response.status == 404
        assert_that(await response.json(), equal_to({"detail": "Game or organizer not found"}))

    assert_that(await (await client.get(collection)).json(), equal_to([{"user_id": owner}]))


async def test_delete_game_removes_participant_rows_and_preserves_users(
    *, client: aiohttp.ClientSession, connection: asyncpg.Connection, created_game: dict[str, Any]
) -> None:
    # arrange
    user_id = await _register_user(client)
    path = _path(created_game)
    assert (await client.put(f"{path}/participants/{user_id}")).status == 204

    # act
    response = await client.delete(path)

    # assert
    assert response.status == 204
    assert (await client.get(f"{path}/participants")).status == 404
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM santa_game_participants WHERE game_id = $1", UUID(created_game["id"])
        )
        == 0
    )
    assert await connection.fetchval("SELECT count(*) FROM users WHERE id = $1", user_id) == 1


async def test_games_require_bearer_and_old_group_routes_are_removed(
    *, running_backend: BackendEnvironment, client: aiohttp.ClientSession, created_game: dict[str, Any]
) -> None:
    # arrange
    game = _path(created_game)
    owner = created_game["organizer_user_id"]

    # act
    async with aiohttp.ClientSession(base_url=running_backend.api_url) as anonymous:
        game_response = await anonymous.get(game)
        participants_response = await anonymous.put(f"{game}/participants/{owner}")
    former_route = await client.get(game.replace("/games/", "/groups/"))

    # assert
    assert game_response.status == participants_response.status == 401
    assert former_route.status == 404
    assert_that(await former_route.json(), equal_to({"detail": "Not Found"}))


async def test_game_openapi_has_stable_operations_and_response_schemas(client: aiohttp.ClientSession) -> None:
    # act
    response = await client.get("/openapi.json")

    # assert
    assert response.status == 200
    schema = await response.json()
    collection = "/api/v1/users/{organizer_user_id}/games"
    game = collection + "/{game_id}"
    participants = game + "/participants"
    participant = participants + "/{user_id}"
    game_model = {"$ref": "#/components/schemas/GameResponse"}
    participant_model = {"$ref": "#/components/schemas/GameParticipantResponse"}
    for method, path, operation_id, status_code, response_model in [
        ("post", collection, "create_game", "201", game_model),
        ("get", collection, "list_games", "200", {"type": "array", "items": game_model}),
        ("get", game, "get_game", "200", game_model),
        ("patch", game, "update_game", "200", game_model),
        ("delete", game, "delete_game", "204", None),
        ("get", participants, "list_game_participants", "200", {"type": "array", "items": participant_model}),
        ("put", participant, "add_game_participant", "204", None),
        ("delete", participant, "remove_game_participant", "204", None),
    ]:
        operation = schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert "игр" in operation["description"].lower()
        assert_that(operation["security"], equal_to([{"ServiceBearer": []}]))
        assert {"401", "404", "422", "503"} <= operation["responses"].keys()
        successful_response = operation["responses"][status_code]
        if response_model is None:
            assert "content" not in successful_response
        else:
            assert_that(successful_response["content"]["application/json"]["schema"], has_entries(response_model))

    assert all("/groups" not in path for path in schema["paths"])
    models = schema["components"]["schemas"]
    assert_that(
        set(models["GameResponse"]["required"]),
        equal_to(
            {"id", "organizer_user_id", "name", "description", "starts_at", "ends_at", "created_at", "updated_at"}
        ),
    )
    assert models["GameUpdateRequest"]["additionalProperties"] is False
    assert models["GameUpdateRequest"]["properties"]["name"]["type"] == "string"
    for model in ("GameCreateRequest", "GameUpdateRequest", "GameResponse"):
        for field in ("starts_at", "ends_at"):
            assert_that(
                models[model]["properties"][field]["anyOf"],
                equal_to([{"format": "date-time", "type": "string"}, {"type": "null"}]),
            )
