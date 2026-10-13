from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_current_session: ContextVar[AsyncSession | None] = ContextVar("postgres_session", default=None)


class PostgresSessionUnavailableError(RuntimeError):
    """Raised when a repository is called outside a PostgreSQL transaction."""


def get() -> AsyncSession:
    session = _current_session.get()
    if session is None:
        raise PostgresSessionUnavailableError("PostgreSQL session is only available inside transaction()")

    return session


class PostgresDatabase:
    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        if _current_session.get() is not None:
            raise PostgresSessionUnavailableError("Nested PostgreSQL transactions are not supported")

        async with self._session_factory() as session:
            token = _current_session.set(session)
            try:
                async with session.begin():
                    yield
            finally:
                _current_session.reset(token)

    async def is_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except OSError, ConnectionError:
            return False

        return True

    async def close(self) -> None:
        await self._engine.dispose()
