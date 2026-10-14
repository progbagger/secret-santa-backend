import pytest

from secret_santa_backend.repositories.postgres import PostgresSessionUnavailableError, get


def test_get_raises_diagnostic_error_outside_transaction() -> None:
    with pytest.raises(PostgresSessionUnavailableError, match="only available inside transaction"):
        get()
