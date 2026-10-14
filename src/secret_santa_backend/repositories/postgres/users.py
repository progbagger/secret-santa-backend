from sqlalchemy import RowMapping, select
from sqlalchemy.dialects.postgresql import insert

from secret_santa_backend.entities import RegistrationResult, User
from secret_santa_backend.repositories.postgres.database import get
from secret_santa_backend.repositories.postgres.schema import users


async def register_user(login: str) -> RegistrationResult:
    session = get()
    statement = (
        insert(users).values(login=login).on_conflict_do_nothing(index_elements=[users.c.login]).returning(*users.c)
    )
    row = (await session.execute(statement)).mappings().one_or_none()
    created = row is not None

    if row is None:
        row = (await session.execute(select(users).where(users.c.login == login))).mappings().one()

    return RegistrationResult(user=_to_user(row), created=created)


def _to_user(row: RowMapping) -> User:
    return User(
        id=row["id"],
        login=row["login"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
