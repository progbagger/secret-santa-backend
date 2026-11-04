from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from secret_santa_backend.config import Settings
from secret_santa_backend.repositories.postgres import PostgresDatabase, get_database, use_database
from secret_santa_backend.services import register_user as register_user_service
from secret_santa_backend.services.groups import GroupNotFoundError
from secret_santa_backend.web.entities import HealthResponse, UserRegistrationRequest, UserResponse
from secret_santa_backend.web.groups import create_group_router


def create_app() -> FastAPI:
    settings = Settings.from_environment()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[dict[str, PostgresDatabase]]:
        database = PostgresDatabase(settings.database_url)
        try:
            yield {"database": database}
        finally:
            await database.close()

    async def bind_database(request: Request) -> AsyncIterator[None]:
        # Lifespan state is copied to requests; its ContextVars are not inherited by HTTP tasks.
        database: PostgresDatabase = request.state.database
        with use_database(database):
            yield

    app = FastAPI(
        title="Secret Santa Backend",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        dependencies=[Depends(bind_database)],
    )
    app.include_router(create_group_router())

    @app.exception_handler(GroupNotFoundError)
    async def group_not_found(_: Request, __: GroupNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Group or organizer not found"})

    @app.get("/health/live", operation_id="get_liveness", response_model=HealthResponse)
    async def get_liveness() -> HealthResponse:
        """Проверить, что процесс backend запущен, без запроса к PostgreSQL."""
        return HealthResponse(status="ok")

    @app.get(
        "/health/ready",
        operation_id="get_readiness",
        response_model=HealthResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    )
    async def get_readiness() -> HealthResponse | JSONResponse:
        """Проверить готовность backend: при недоступной PostgreSQL вернуть 503."""
        try:
            ready = await get_database().is_ready()
        except SQLAlchemyError:
            ready = False

        if not ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=HealthResponse(status="unavailable").model_dump(),
            )

        return HealthResponse(status="ok")

    @app.post(
        "/api/v1/users",
        operation_id="register_user",
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED,
        responses={status.HTTP_200_OK: {"model": UserResponse}},
    )
    async def register_user(*, payload: UserRegistrationRequest, response: Response) -> UserResponse:
        """Зарегистрировать пользователя по логину: 201 при создании, 200 при повторном запросе."""
        result = await register_user_service(payload.login)
        if not result.created:
            response.status_code = status.HTTP_200_OK

        return UserResponse(
            id=result.user.id,
            login=result.user.login,
            created_at=result.user.created_at,
            updated_at=result.user.updated_at,
        )

    return app
