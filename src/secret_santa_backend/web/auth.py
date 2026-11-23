import re

import aiohttp
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

API_PREFIX = "/api/v1"
_BEARER = re.compile(r"Bearer +([A-Za-z0-9_-]{43})", re.ASCII | re.IGNORECASE)


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


class BearerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if not is_api_path(request.url.path):
            await self.app(scope, receive, send)
            return

        headers = request.headers.getlist("authorization")
        match = _BEARER.fullmatch(headers[0]) if len(headers) == 1 else None
        if match is not None:
            try:
                client: aiohttp.ClientSession = request.state.token_client
                async with client.post("/introspect", data={"token": match[1]}, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ValueError("Unexpected introspection status")
                    result = await response.json()
                if not isinstance(result, dict) or type(result.get("active")) is not bool:
                    raise ValueError("Invalid introspection response")
            except aiohttp.ClientError, TimeoutError, ValueError:
                await JSONResponse({"detail": "Token verification unavailable"}, status_code=503)(scope, receive, send)
                return

            if result["active"]:
                await self.app(scope, receive, send)
                return

        await JSONResponse(
            {"detail": "Invalid or missing bearer token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )(scope, receive, send)
