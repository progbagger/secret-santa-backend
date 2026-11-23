import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic

from secret_santa_backend.config import IssuerSettings

TOKEN_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_BOT = "bot"
_BACKEND = "backend"


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(*, now: Callable[[], datetime] = utc_now) -> FastAPI:
    settings = IssuerSettings.from_environment()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    basic_auth = HTTPBasic(auto_error=False)
    expiry_by_digest: dict[bytes, datetime] = {}

    async def authenticated(request: Request, client_id: str, secret: str) -> bool:
        if len(request.headers.getlist("authorization")) != 1:
            return False

        try:
            credentials = await basic_auth(request)
        except HTTPException:
            return False
        if credentials is None:
            return False

        secret_matches = secrets.compare_digest(credentials.password, secret)
        return credentials.username == client_id and secret_matches

    @app.get("/health/ready")
    async def readiness() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/token")
    async def issue_token(request: Request) -> JSONResponse:
        if not await authenticated(request, _BOT, settings.bot_secret):
            return _invalid_client()

        grant_type = await _form_value(request, "grant_type")
        if grant_type != "client_credentials":
            error = "invalid_request" if grant_type is None else "unsupported_grant_type"
            return JSONResponse({"error": error}, status_code=400, headers=TOKEN_HEADERS)

        issued_at = now()
        for digest, expires_at in list(expiry_by_digest.items()):
            if expires_at <= issued_at:
                del expiry_by_digest[digest]

        token = secrets.token_urlsafe(32)
        expiry_by_digest[_digest(token)] = issued_at + timedelta(seconds=settings.ttl_seconds)
        return JSONResponse(
            {"access_token": token, "token_type": "Bearer", "expires_in": settings.ttl_seconds},
            headers=TOKEN_HEADERS,
        )

    @app.post("/introspect")
    async def introspect(request: Request) -> JSONResponse:
        if not await authenticated(request, _BACKEND, settings.backend_secret):
            return _invalid_client()

        token = await _form_value(request, "token")
        if token is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=TOKEN_HEADERS)

        digest = _digest(token)
        expires_at = expiry_by_digest.get(digest)
        active = expires_at is not None and expires_at > now()
        if not active:
            expiry_by_digest.pop(digest, None)

        return JSONResponse({"active": active}, headers=TOKEN_HEADERS)

    return app


async def _form_value(request: Request, field: str) -> str | None:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None

    async with request.form() as form:
        values = form.getlist(field)
    if len(values) == 1 and isinstance(value := values[0], str) and value:
        return value
    return None


def _invalid_client() -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_client"},
        status_code=401,
        headers={**TOKEN_HEADERS, "WWW-Authenticate": "Basic"},
    )


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
