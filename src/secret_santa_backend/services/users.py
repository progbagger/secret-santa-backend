from secret_santa_backend.entities import RegistrationResult
from secret_santa_backend.repositories.postgres import PostgresDatabase
from secret_santa_backend.repositories.postgres import register_user as persist_user


async def register_user(database: PostgresDatabase, login: str) -> RegistrationResult:
    async with database.transaction():
        return await persist_user(login)
