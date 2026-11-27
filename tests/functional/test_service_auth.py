import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from hamcrest import assert_that, equal_to, has_entries

from tests.functional.conftest import ROOT, TOKEN_ENV, BackendEnvironment

_USERS = "/api/v1/users"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Authorization", "Basic dGVzdA==")],
        [("Authorization", "Bearer short")],
        [("Authorization", f"Bearer {'x' * 43}")],
        [("Authorization", f"Bearer {'x' * 43}"), ("Authorization", f"Bearer {'y' * 43}")],
    ],
)
async def test_invalid_credentials_are_rejected_before_request_validation(
    backend_without_postgres: BackendEnvironment, headers: list[tuple[str, str]]
) -> None:
    # arrange
    url = f"{backend_without_postgres.api_url}{_USERS}"

    # act
    async with aiohttp.ClientSession() as client, client.post(url, headers=headers, json={}) as response:
        await response.read()

    # assert
    assert response.status == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert_that((await response.json()), equal_to({"detail": "Invalid or missing bearer token"}))


async def test_service_credentials_are_specific_to_each_issuer_operation(
    backend_without_postgres: BackendEnvironment,
) -> None:
    # arrange
    environment = backend_without_postgres

    # act
    wrong_secret = await environment.request_token(secret="wrong")
    wrong_role = await environment.request_token(role="backend", secret=TOKEN_ENV["TOKEN_BACKEND_CLIENT_SECRET"])
    auth = aiohttp.encode_basic_auth("bot", TOKEN_ENV["TOKEN_BOT_CLIENT_SECRET"])
    async with (
        aiohttp.ClientSession(base_url=environment.issuer_url) as client,
        client.post("/introspect", headers={"Authorization": auth}, data={"token": "some-token"}) as response,
    ):
        await response.read()
        introspection = response

    # assert
    for response in (wrong_secret, wrong_role, introspection):
        assert response.status == 401
        assert_that((await response.json()), equal_to({"error": "invalid_client"}))


async def test_renewal_allows_overlapping_tokens_until_exact_expiry(
    backend_without_postgres: BackendEnvironment,
) -> None:
    # arrange
    environment = backend_without_postgres
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    environment.set_issuer_time(issued_at)

    # act
    first = await environment.request_token()
    first.raise_for_status()
    first_token = (await first.json())["access_token"]

    environment.set_issuer_time(issued_at + timedelta(seconds=899))
    second = await environment.request_token()
    second.raise_for_status()
    second_token = (await second.json())["access_token"]

    async with aiohttp.ClientSession(base_url=environment.api_url) as client:
        before_expiry = [
            await client.post(_USERS, headers=_bearer(token), json={}) for token in (first_token, second_token)
        ]

        environment.set_issuer_time(issued_at + timedelta(seconds=900))
        expired = await client.post(_USERS, headers=_bearer(first_token), json={})
        current = await client.post(_USERS, headers=_bearer(second_token), json={})

        third = await environment.request_token()
        third.raise_for_status()
        renewed = await client.post(_USERS, headers=_bearer((await third.json())["access_token"]), json={})

    # assert
    assert first.headers["Cache-Control"] == "no-store"
    assert_that((await first.json()), has_entries(token_type="Bearer", expires_in=900))
    assert second_token != first_token
    assert_that([response.status for response in before_expiry], equal_to([422, 422]))
    assert expired.status == 401
    assert expired.headers["WWW-Authenticate"] == "Bearer"
    assert current.status == renewed.status == 422


async def test_issuer_outage_fails_closed_and_client_can_renew_after_restart(
    backend_without_postgres: BackendEnvironment,
) -> None:
    # arrange
    environment = backend_without_postgres
    first = await environment.request_token()
    first.raise_for_status()
    headers = _bearer((await first.json())["access_token"])

    # act
    await asyncio.to_thread(environment.stop_local_issuer)
    async with aiohttp.ClientSession(base_url=environment.api_url) as client:
        unavailable = await client.post(_USERS, headers=headers, json={})
        liveness = await client.get("/health/live")
        readiness = await client.get("/health/ready")

        await asyncio.to_thread(environment.start_local_issuer)
        old_token = await client.post(_USERS, headers=headers, json={})
        renewed = await environment.request_token()
        renewed.raise_for_status()
        new_token = await client.post(_USERS, headers=_bearer((await renewed.json())["access_token"]), json={})

    # assert
    assert unavailable.status == readiness.status == 503
    assert liveness.status == 200
    assert old_token.status == 401
    assert new_token.status == 422


async def test_openapi_declares_service_bearer_only_for_business_paths(
    backend_without_postgres: BackendEnvironment,
) -> None:
    # arrange
    url = f"{backend_without_postgres.api_url}/openapi.json"

    # act
    async with aiohttp.ClientSession() as client, client.get(url) as response:
        await response.read()

    # assert
    assert response.status == 200
    schema = await response.json()

    assert_that(
        schema["components"]["securitySchemes"]["ServiceBearer"],
        equal_to({"type": "http", "scheme": "bearer"}),
    )
    for path, path_item in schema["paths"].items():
        for operation in path_item.values():
            if path.startswith("/api/v1/"):
                assert_that(operation["security"], equal_to([{"ServiceBearer": []}]))
                assert {"401", "503"} <= operation["responses"].keys()
            else:
                assert "security" not in operation


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("TOKEN_ISSUER_URL", None, "TOKEN_ISSUER_URL environment variable is required"),
        ("TOKEN_ISSUER_URL", "", "TOKEN_ISSUER_URL environment variable is required"),
        ("TOKEN_ISSUER_URL", "https://user:invalid-secret@localhost", "TOKEN_ISSUER_URL must be an HTTP(S) origin"),
        ("TOKEN_BACKEND_CLIENT_SECRET", "", "TOKEN_BACKEND_CLIENT_SECRET must be a URL-safe secret"),
        (
            "TOKEN_BACKEND_CLIENT_SECRET",
            "invalid-secret-should-be-redacted!",
            "TOKEN_BACKEND_CLIENT_SECRET must be a URL-safe secret",
        ),
    ],
)
def test_backend_startup_requires_valid_issuer_configuration(name: str, value: str | None, message: str) -> None:
    # arrange
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://localhost/test",
        "TOKEN_ISSUER_URL": "http://127.0.0.1:8001",
        **TOKEN_ENV,
    }
    if value is None:
        env.pop(name)
    else:
        env[name] = value

    # act
    result = subprocess.run(
        [sys.executable, "-m", "secret_santa_backend"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        timeout=10,
        text=True,
    )

    # assert
    assert result.returncode != 0
    assert message in result.stderr
    if value:
        assert value not in result.stderr
