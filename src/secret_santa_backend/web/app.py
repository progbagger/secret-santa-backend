from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from secret_santa_backend.config import Settings
from secret_santa_backend.repositories.postgres import PostgresDatabase
from secret_santa_backend.services import register_user as register_user_service
from secret_santa_backend.web.entities import HealthResponse, UserRegistrationRequest, UserResponse


def create_app() -> FastAPI:
    settings = Settings.from_environment()
    database = PostgresDatabase(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await database.close()

    app = FastAPI(
        title="Secret Santa Backend",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/health/live", operation_id="get_liveness", response_model=HealthResponse)
    async def get_liveness() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/health/ready",
        operation_id="get_readiness",
        response_model=HealthResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    )
    async def get_readiness() -> HealthResponse | JSONResponse:
        try:
            ready = await database.is_ready()
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
    async def register_user(payload: UserRegistrationRequest, response: Response) -> UserResponse:
        result = await register_user_service(database, payload.login)
        if not result.created:
            response.status_code = status.HTTP_200_OK

        return UserResponse(
            id=result.user.id,
            login=result.user.login,
            created_at=result.user.created_at,
            updated_at=result.user.updated_at,
        )

    return app
