from uuid import UUID, uuid4

from sqlalchemy import RowMapping, delete, func, insert, or_, select, update

from secret_santa_backend.entities.group import Group, GroupUpdate
from secret_santa_backend.repositories.postgres.database import get
from secret_santa_backend.repositories.postgres.schema import santa_groups


async def create_group(*, organizer_user_id: int, name: str, description: str | None) -> Group:
    statement = (
        insert(santa_groups)
        .values(id=uuid4(), organizer_user_id=organizer_user_id, name=name, description=description)
        .returning(*santa_groups.c)
    )
    row = (await get().execute(statement)).mappings().one()
    return _to_group(row)


async def get_group(*, organizer_user_id: int, group_id: UUID) -> Group | None:
    statement = select(santa_groups).where(
        santa_groups.c.id == group_id, santa_groups.c.organizer_user_id == organizer_user_id
    )
    row = (await get().execute(statement)).mappings().one_or_none()
    return _to_group(row) if row is not None else None


async def update_group(*, organizer_user_id: int, group_id: UUID, changes: GroupUpdate) -> Group | None:
    values: dict[str, str | None] = {}
    if changes.name is not None:
        values["name"] = changes.name
    if changes.description_provided:
        values["description"] = changes.description

    statement = (
        update(santa_groups)
        .where(
            santa_groups.c.id == group_id,
            santa_groups.c.organizer_user_id == organizer_user_id,
            or_(*(santa_groups.c[key].is_distinct_from(value) for key, value in values.items())),
        )
        .values(**values, updated_at=func.now())
        .returning(*santa_groups.c)
    )
    row = (await get().execute(statement)).mappings().one_or_none()
    if row is None:
        return await get_group(organizer_user_id=organizer_user_id, group_id=group_id)

    return _to_group(row)


async def delete_group(*, organizer_user_id: int, group_id: UUID) -> bool:
    statement = (
        delete(santa_groups)
        .where(santa_groups.c.id == group_id, santa_groups.c.organizer_user_id == organizer_user_id)
        .returning(santa_groups.c.id)
    )
    return (await get().execute(statement)).scalar_one_or_none() is not None


def _to_group(row: RowMapping) -> Group:
    return Group(
        id=row["id"],
        organizer_user_id=row["organizer_user_id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
