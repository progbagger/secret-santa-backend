from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypedDict

import aiohttp
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from secret_santa_backend.config import Settings
from secret_santa_backend.repositories.postgres import PostgresDatabase, get_database, use_database
from secret_santa_backend.services import register_user as register_user_service
from secret_santa_backend.services.games import (
    GameNotFoundError,
    InvalidGameScheduleError,
    ParticipantUserNotFoundError,
)
from secret_santa_backend.web.auth import BearerMiddleware, is_api_path
from secret_santa_backend.web.entities import HealthResponse, UserRegistrationRequest, UserResponse
from secret_santa_backend.web.games import create_game_router


class AppState(TypedDict):
    database: PostgresDatabase
    token_client: aiohttp.ClientSession


class BackendAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is not None:
            return self.openapi_schema

        schema = super().openapi()
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["ServiceBearer"] = {"type": "http", "scheme": "bearer"}
        for path, path_item in schema["paths"].items():
            if not is_api_path(path):
                continue
            for operation in path_item.values():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["security"] = [{"ServiceBearer": []}]
                    operation["responses"]["401"] = {"description": "Invalid or missing bearer token"}
                    operation["responses"]["503"] = {"description": "Token verification unavailable"}

        return schema


def create_app() -> FastAPI:
    settings = Settings.from_environment()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[AppState]:
        database = PostgresDatabase(settings.database_url)
        try:
            async with aiohttp.ClientSession(
                base_url=settings.issuer_url,
                headers={"Authorization": aiohttp.encode_basic_auth("backend", settings.backend_secret)},
                timeout=aiohttp.ClientTimeout(total=2),
            ) as token_client:
                yield {"database": database, "token_client": token_client}
        finally:
            await database.close()

    async def bind_database(request: Request) -> AsyncIterator[None]:
        # Lifespan state is copied to requests; its ContextVars are not inherited by HTTP tasks.
        database: PostgresDatabase = request.state.database
        with use_database(database):
            yield

    app = BackendAPI(
        title="Secret Santa Backend",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        dependencies=[Depends(bind_database)],
    )
    app.add_middleware(BearerMiddleware)
    app.include_router(create_game_router())

    @app.exception_handler(GameNotFoundError)
    async def game_not_found(_: Request, __: GameNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Game or organizer not found"})

    @app.exception_handler(ParticipantUserNotFoundError)
    async def participant_user_not_found(_: Request, __: ParticipantUserNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "User not found"})

    @app.exception_handler(InvalidGameScheduleError)
    async def invalid_game_schedule(_: Request, __: InvalidGameScheduleError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": [{"type": "value_error", "loc": ["body"], "msg": "ends_at must be later than starts_at"}],
            },
        )

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
    async def get_readiness(request: Request) -> HealthResponse | JSONResponse:
        """Проверить доступность PostgreSQL и сервиса выдачи токенов."""
        try:
            ready = await get_database().is_ready()
            if ready:
                token_client: aiohttp.ClientSession = request.state.token_client
                async with token_client.get("/health/ready", allow_redirects=False) as response:
                    ready = response.status == status.HTTP_200_OK
        except SQLAlchemyError, aiohttp.ClientError, TimeoutError:
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
