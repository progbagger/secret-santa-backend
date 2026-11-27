from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from secret_santa_backend.entities.game import Game
from secret_santa_backend.repositories.postgres.database import get
from secret_santa_backend.repositories.postgres.schema import santa_game_participants, santa_games


async def create_game(
    *,
    organizer_user_id: int,
    name: str,
    description: str | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> Game:
    statement = (
        insert(santa_games)
        .values(
            id=uuid4(),
            organizer_user_id=organizer_user_id,
            name=name,
            description=description,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        .returning(*santa_games.c)
    )
    row = (await get().execute(statement)).mappings().one()
    return _to_game(row)


async def list_games(organizer_user_id: int) -> list[Game]:
    statement = (
        select(santa_games)
        .where(santa_games.c.organizer_user_id == organizer_user_id)
        .order_by(santa_games.c.created_at, santa_games.c.id)
    )
    rows = (await get().execute(statement)).mappings()
    return [_to_game(row) for row in rows]


async def get_game(*, organizer_user_id: int, game_id: UUID, for_update: bool = False) -> Game | None:
    statement = select(santa_games).where(
        santa_games.c.id == game_id, santa_games.c.organizer_user_id == organizer_user_id
    )
    if for_update:
        statement = statement.with_for_update()

    row = (await get().execute(statement)).mappings().one_or_none()
    return _to_game(row) if row is not None else None


async def update_game(game: Game) -> Game:
    statement = (
        update(santa_games)
        .where(santa_games.c.id == game.id, santa_games.c.organizer_user_id == game.organizer_user_id)
        .values(
            name=game.name,
            description=game.description,
            starts_at=game.starts_at,
            ends_at=game.ends_at,
            updated_at=func.clock_timestamp(),
        )
        .returning(*santa_games.c)
    )
    row = (await get().execute(statement)).mappings().one()
    return _to_game(row)


async def delete_game(*, organizer_user_id: int, game_id: UUID) -> bool:
    statement = (
        delete(santa_games)
        .where(santa_games.c.id == game_id, santa_games.c.organizer_user_id == organizer_user_id)
        .returning(santa_games.c.id)
    )
    return (await get().execute(statement)).scalar_one_or_none() is not None


async def lock_game_key(*, organizer_user_id: int, game_id: UUID) -> bool:
    statement = (
        select(santa_games.c.id)
        .where(santa_games.c.id == game_id, santa_games.c.organizer_user_id == organizer_user_id)
        .with_for_update(read=True, key_share=True)
    )
    return (await get().execute(statement)).scalar_one_or_none() is not None


async def list_participants(game_id: UUID) -> list[int]:
    statement = (
        select(santa_game_participants.c.user_id)
        .where(santa_game_participants.c.game_id == game_id)
        .order_by(santa_game_participants.c.user_id)
    )
    return list((await get().execute(statement)).scalars())


async def add_participant(*, game_id: UUID, user_id: int) -> None:
    statement = (
        pg_insert(santa_game_participants)
        .values(game_id=game_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=[santa_game_participants.c.game_id, santa_game_participants.c.user_id])
    )
    await get().execute(statement)


async def remove_participant(*, game_id: UUID, user_id: int) -> None:
    statement = delete(santa_game_participants).where(
        santa_game_participants.c.game_id == game_id, santa_game_participants.c.user_id == user_id
    )
    await get().execute(statement)


def _to_game(row: RowMapping) -> Game:
    return Game(
        id=row["id"],
        organizer_user_id=row["organizer_user_id"],
        name=row["name"],
        description=row["description"],
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
