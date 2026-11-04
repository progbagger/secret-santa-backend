from uuid import UUID

from secret_santa_backend.entities.group import Group, GroupUpdate
from secret_santa_backend.repositories.postgres import groups, transaction, users


class GroupNotFoundError(Exception):
    """The group or its organizer is unavailable in the supplied context."""


async def create_group(*, organizer_user_id: int, name: str, description: str | None) -> Group:
    async with transaction():
        if not await users.lock_user(organizer_user_id):
            raise GroupNotFoundError

        return await groups.create_group(
            organizer_user_id=organizer_user_id, name=name.strip(), description=description
        )


async def get_group(*, organizer_user_id: int, group_id: UUID) -> Group:
    async with transaction():
        group = await groups.get_group(organizer_user_id=organizer_user_id, group_id=group_id)
        if group is None:
            raise GroupNotFoundError

        return group


async def update_group(*, organizer_user_id: int, group_id: UUID, changes: GroupUpdate) -> Group:
    async with transaction():
        group = await groups.update_group(organizer_user_id=organizer_user_id, group_id=group_id, changes=changes)
        if group is None:
            raise GroupNotFoundError

        return group


async def delete_group(*, organizer_user_id: int, group_id: UUID) -> None:
    async with transaction():
        if not await groups.delete_group(organizer_user_id=organizer_user_id, group_id=group_id):
            raise GroupNotFoundError
