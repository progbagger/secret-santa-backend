import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_environment(cls) -> Settings:
        try:
            database_url = os.environ["DATABASE_URL"]
        except KeyError as error:
            raise RuntimeError("DATABASE_URL environment variable is required") from error

        host = os.environ.get("BACKEND_HOST", "0.0.0.0")
        port = _read_port(os.environ.get("BACKEND_PORT", "8000"))

        return cls(database_url=database_url, host=host, port=port)


def _read_port(raw_port: str) -> int:
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("BACKEND_PORT must be an integer") from error

    if not 1 <= port <= 65535:
        raise RuntimeError("BACKEND_PORT must be between 1 and 65535")

    return port
