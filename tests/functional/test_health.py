import asyncio

import aiohttp
from hamcrest import assert_that, equal_to

from tests.functional.conftest import BackendEnvironment


async def test_liveness_stays_available_and_readiness_tracks_postgres_and_issuer(
    backend_without_postgres: BackendEnvironment,
) -> None:
    # arrange
    environment = backend_without_postgres

    # act
    async with aiohttp.ClientSession(base_url=environment.api_url) as client:
        liveness_response = await client.get("/health/live")
        without_postgres = await client.get("/health/ready")

        await asyncio.to_thread(environment.start_postgres)
        await asyncio.to_thread(environment.migrate)
        ready_response = await environment.wait_for_http("/health/ready", expected_status=200, timeout=20)

        await asyncio.to_thread(environment.stop_local_issuer)
        liveness_without_issuer = await client.get("/health/live")
        readiness_without_issuer = await client.get("/health/ready")

        await asyncio.to_thread(environment.start_local_issuer)
        restored_readiness = await client.get("/health/ready")
        await liveness_response.read()
        await without_postgres.read()

    # assert
    assert_that(liveness_response.status, equal_to(200))
    assert_that((await liveness_response.json()), equal_to({"status": "ok"}))
    assert_that(without_postgres.status, equal_to(503))
    assert_that((await without_postgres.json()), equal_to({"status": "unavailable"}))
    assert_that((await ready_response.json()), equal_to({"status": "ok"}))
    assert liveness_without_issuer.status == 200
    assert readiness_without_issuer.status == 503
    assert restored_readiness.status == 200
