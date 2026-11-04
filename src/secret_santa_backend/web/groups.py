from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Response, status

from secret_santa_backend.entities.group import GroupUpdate
from secret_santa_backend.services import groups
from secret_santa_backend.web.entities.groups import (
    GroupCreateRequest,
    GroupNotFoundResponse,
    GroupResponse,
    GroupUpdateRequest,
)

OrganizerId = Annotated[int, Path(gt=0, le=2**63 - 1)]


def create_group_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/users/{organizer_user_id}/groups",
        responses={status.HTTP_404_NOT_FOUND: {"model": GroupNotFoundResponse}},
    )

    @router.post("", operation_id="create_group", status_code=status.HTTP_201_CREATED, response_model=GroupResponse)
    async def create_group(*, organizer_user_id: OrganizerId, payload: GroupCreateRequest) -> GroupResponse:
        """Создать группу зарегистрированного организатора.

        Пробелы по краям имени удаляются. Неизвестный организатор получает 404.
        Контекст организатора задаёт доверенный клиент внутреннего API.
        """
        group = await groups.create_group(
            organizer_user_id=organizer_user_id, name=payload.name, description=payload.description
        )
        return GroupResponse.model_validate(group)

    @router.get("/{group_id}", operation_id="get_group", response_model=GroupResponse)
    async def get_group(*, organizer_user_id: OrganizerId, group_id: UUID) -> GroupResponse:
        """Получить группу в контексте её организатора.

        Отсутствующая и чужая группа возвращают одинаковый ответ 404.
        """
        group = await groups.get_group(organizer_user_id=organizer_user_id, group_id=group_id)
        return GroupResponse.model_validate(group)

    @router.patch("/{group_id}", operation_id="update_group", response_model=GroupResponse)
    async def update_group(
        *, organizer_user_id: OrganizerId, group_id: UUID, payload: GroupUpdateRequest
    ) -> GroupResponse:
        """Изменить только явно переданные имя и описание группы.

        null очищает только описание; пустое тело и неизвестные поля дают 422.
        Повтор тех же значений сохраняет updated_at. Владелец группы неизменяем.
        Отсутствующая и чужая группа возвращают одинаковый ответ 404.
        """
        changes = GroupUpdate(
            name=payload.name,
            description=payload.description,
            description_provided="description" in payload.model_fields_set,
        )
        group = await groups.update_group(organizer_user_id=organizer_user_id, group_id=group_id, changes=changes)
        return GroupResponse.model_validate(group)

    @router.delete("/{group_id}", operation_id="delete_group", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_group(*, organizer_user_id: OrganizerId, group_id: UUID) -> Response:
        """Удалить группу организатора и вернуть 204 без тела ответа.

        Пока у группы нет участников и игр, удаление физическое.
        Чужая, отсутствующая или уже удалённая группа возвращает 404.
        """
        await groups.delete_group(organizer_user_id=organizer_user_id, group_id=group_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
