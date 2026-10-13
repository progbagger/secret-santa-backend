import httpx
from hamcrest import assert_that, equal_to

from tests.functional.conftest import BackendEnvironment


def test_liveness_stays_available_and_readiness_tracks_postgres(
    backend_without_postgres: BackendEnvironment,
) -> None:
    with httpx.Client(base_url=backend_without_postgres.api_url) as client:
        liveness_response = client.get("/health/live")
        readiness_response = client.get("/health/ready")

        assert_that(liveness_response.status_code, equal_to(200))
        assert_that(liveness_response.json(), equal_to({"status": "ok"}))
        assert_that(readiness_response.status_code, equal_to(503))
        assert_that(readiness_response.json(), equal_to({"status": "unavailable"}))

        backend_without_postgres.start_postgres()
        backend_without_postgres.migrate()
        ready_response = backend_without_postgres.wait_for_http(
            "/health/ready",
            expected_status=200,
            timeout=20,
        )

        assert_that(ready_response.json(), equal_to({"status": "ok"}))
