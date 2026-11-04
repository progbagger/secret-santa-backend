from secret_santa_backend.entities import RegistrationResult
from secret_santa_backend.repositories.postgres import transaction
from secret_santa_backend.repositories.postgres.users import register_user as persist_user


async def register_user(login: str) -> RegistrationResult:
    async with transaction():
        return await persist_user(login)
