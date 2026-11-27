from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Response, status

from secret_santa_backend.entities.game import GameUpdate
from secret_santa_backend.services import games
from secret_santa_backend.web.entities.games import (
    GameCreateRequest,
    GameNotFoundResponse,
    GameParticipantResponse,
    GameResponse,
    GameUpdateRequest,
)

OrganizerId = Annotated[int, Path(gt=0, le=2**63 - 1)]
ParticipantId = Annotated[int, Path(gt=0, le=2**63 - 1)]


def create_game_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/users/{organizer_user_id}/games",
        responses={status.HTTP_404_NOT_FOUND: {"model": GameNotFoundResponse}},
    )

    @router.post("", operation_id="create_game", status_code=status.HTTP_201_CREATED, response_model=GameResponse)
    async def create_game(*, organizer_user_id: OrganizerId, payload: GameCreateRequest) -> GameResponse:
        """Создать игру зарегистрированного организатора.

        Пробелы по краям имени удаляются. Неизвестный организатор получает 404.
        Сроки необязательны. Если заданы оба, окончание должно быть позже начала.
        Контекст организатора задаёт доверенный клиент внутреннего API.
        """
        game = await games.create_game(
            organizer_user_id=organizer_user_id,
            name=payload.name,
            description=payload.description,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
        return GameResponse.model_validate(game)

    @router.get("", operation_id="list_games", response_model=list[GameResponse])
    async def list_games(*, organizer_user_id: OrganizerId) -> list[GameResponse]:
        """Получить игры организатора в порядке даты создания и UUID.

        Неизвестный организатор получает 404.
        """
        return [GameResponse.model_validate(game) for game in await games.list_games(organizer_user_id)]

    @router.get("/{game_id}", operation_id="get_game", response_model=GameResponse)
    async def get_game(*, organizer_user_id: OrganizerId, game_id: UUID) -> GameResponse:
        """Получить игру в контексте её организатора.

        Отсутствующая и чужая игра возвращают одинаковый ответ 404.
        """
        game = await games.get_game(organizer_user_id=organizer_user_id, game_id=game_id)
        return GameResponse.model_validate(game)

    @router.patch("/{game_id}", operation_id="update_game", response_model=GameResponse)
    async def update_game(*, organizer_user_id: OrganizerId, game_id: UUID, payload: GameUpdateRequest) -> GameResponse:
        """Изменить переданные имя, описание или сроки игры.

        null очищает описание или соответствующий срок. Пустое тело, неизвестные поля
        и итоговое окончание в момент начала или ранее дают 422.
        Повтор тех же значений сохраняет updated_at. Владелец игры неизменяем.
        Отсутствующая и чужая игра возвращают одинаковый ответ 404.
        """
        changes = GameUpdate(
            name=payload.name,
            description=payload.description,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            provided_fields=frozenset(payload.model_fields_set),
        )
        game = await games.update_game(organizer_user_id=organizer_user_id, game_id=game_id, changes=changes)
        return GameResponse.model_validate(game)

    @router.delete("/{game_id}", operation_id="delete_game", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_game(*, organizer_user_id: OrganizerId, game_id: UUID) -> Response:
        """Удалить игру организатора и вернуть 204 без тела ответа.

        Игра удаляется вместе с составом.
        Чужая, отсутствующая или уже удалённая игра возвращает 404.
        """
        await games.delete_game(organizer_user_id=organizer_user_id, game_id=game_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/{game_id}/participants", operation_id="list_game_participants", response_model=list[GameParticipantResponse]
    )
    async def list_participants(*, organizer_user_id: OrganizerId, game_id: UUID) -> list[GameParticipantResponse]:
        """Получить состав игры по возрастанию внутреннего ID пользователя."""
        user_ids = await games.list_participants(organizer_user_id=organizer_user_id, game_id=game_id)
        return [GameParticipantResponse(user_id=user_id) for user_id in user_ids]

    @router.put(
        "/{game_id}/participants/{user_id}", operation_id="add_game_participant", status_code=status.HTTP_204_NO_CONTENT
    )
    async def add_participant(*, organizer_user_id: OrganizerId, game_id: UUID, user_id: ParticipantId) -> Response:
        """Добавить зарегистрированного пользователя в состав игры.

        Повторное добавление возвращает 204. Неизвестный пользователь получает 404.
        """
        await games.add_participant(organizer_user_id=organizer_user_id, game_id=game_id, user_id=user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete(
        "/{game_id}/participants/{user_id}",
        operation_id="remove_game_participant",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def remove_participant(*, organizer_user_id: OrganizerId, game_id: UUID, user_id: ParticipantId) -> Response:
        """Исключить пользователя из состава игры. Если он уже отсутствует, вернуть 204."""
        await games.remove_participant(organizer_user_id=organizer_user_id, game_id=game_id, user_id=user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
