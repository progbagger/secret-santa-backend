from dataclasses import replace
from datetime import datetime
from uuid import UUID

from secret_santa_backend.entities.game import Game, GameUpdate
from secret_santa_backend.repositories.postgres import games, transaction, users


class GameNotFoundError(Exception):
    """The game or its organizer is unavailable in the supplied context."""


class ParticipantUserNotFoundError(Exception):
    """The requested participant is not a registered user."""


class InvalidGameScheduleError(Exception):
    """The game ends at or before it starts."""


async def create_game(
    *,
    organizer_user_id: int,
    name: str,
    description: str | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> Game:
    async with transaction():
        if not await users.lock_user(organizer_user_id):
            raise GameNotFoundError

        _validate_schedule(starts_at=starts_at, ends_at=ends_at)
        return await games.create_game(
            organizer_user_id=organizer_user_id,
            name=name.strip(),
            description=description,
            starts_at=starts_at,
            ends_at=ends_at,
        )


async def list_games(organizer_user_id: int) -> list[Game]:
    async with transaction():
        if not await users.lock_user(organizer_user_id):
            raise GameNotFoundError

        return await games.list_games(organizer_user_id)


async def get_game(*, organizer_user_id: int, game_id: UUID) -> Game:
    async with transaction():
        game = await games.get_game(organizer_user_id=organizer_user_id, game_id=game_id)
        if game is None:
            raise GameNotFoundError

        return game


async def update_game(*, organizer_user_id: int, game_id: UUID, changes: GameUpdate) -> Game:
    async with transaction():
        game = await games.get_game(organizer_user_id=organizer_user_id, game_id=game_id, for_update=True)
        if game is None:
            raise GameNotFoundError

        updated = replace(
            game,
            name=game.name if changes.name is None else changes.name,
            description=changes.description if "description" in changes.provided_fields else game.description,
            starts_at=changes.starts_at if "starts_at" in changes.provided_fields else game.starts_at,
            ends_at=changes.ends_at if "ends_at" in changes.provided_fields else game.ends_at,
        )
        _validate_schedule(starts_at=updated.starts_at, ends_at=updated.ends_at)
        if updated == game:
            return game

        return await games.update_game(updated)


async def delete_game(*, organizer_user_id: int, game_id: UUID) -> None:
    async with transaction():
        if not await games.delete_game(organizer_user_id=organizer_user_id, game_id=game_id):
            raise GameNotFoundError


async def list_participants(*, organizer_user_id: int, game_id: UUID) -> list[int]:
    async with transaction():
        await _require_game_key(organizer_user_id=organizer_user_id, game_id=game_id)

        return await games.list_participants(game_id)


async def add_participant(*, organizer_user_id: int, game_id: UUID, user_id: int) -> None:
    async with transaction():
        await _require_game_key(organizer_user_id=organizer_user_id, game_id=game_id)
        if not await users.lock_user(user_id):
            raise ParticipantUserNotFoundError

        await games.add_participant(game_id=game_id, user_id=user_id)


async def remove_participant(*, organizer_user_id: int, game_id: UUID, user_id: int) -> None:
    async with transaction():
        await _require_game_key(organizer_user_id=organizer_user_id, game_id=game_id)

        await games.remove_participant(game_id=game_id, user_id=user_id)


async def _require_game_key(*, organizer_user_id: int, game_id: UUID) -> None:
    if not await games.lock_game_key(organizer_user_id=organizer_user_id, game_id=game_id):
        raise GameNotFoundError


def _validate_schedule(*, starts_at: datetime | None, ends_at: datetime | None) -> None:
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise InvalidGameScheduleError
