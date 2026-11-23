import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    issuer_url: str
    backend_secret: str = field(repr=False)
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = database_url_from_environment()

        host = os.environ.get("BACKEND_HOST", "0.0.0.0")
        port = _read_port(os.environ.get("BACKEND_PORT", "8000"))

        issuer_url = os.environ.get("TOKEN_ISSUER_URL")
        if not issuer_url or not issuer_url.strip():
            raise RuntimeError("TOKEN_ISSUER_URL environment variable is required")

        try:
            parsed_url = urlsplit(issuer_url)
            port_number = parsed_url.port
        except ValueError:
            raise RuntimeError("TOKEN_ISSUER_URL must be an HTTP(S) origin") from None
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or port_number == 0
            or parsed_url.username is not None
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.path not in {"", "/"}
        ):
            raise RuntimeError("TOKEN_ISSUER_URL must be an HTTP(S) origin")

        return cls(
            database_url=database_url,
            issuer_url=issuer_url,
            backend_secret=_read_secret("TOKEN_BACKEND_CLIENT_SECRET"),
            host=host,
            port=port,
        )


def database_url_from_environment() -> str:
    try:
        return os.environ["DATABASE_URL"]
    except KeyError:
        raise RuntimeError("DATABASE_URL environment variable is required") from None


def _read_port(raw_port: str) -> int:
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("BACKEND_PORT must be an integer") from error

    if not 1 <= port <= 65535:
        raise RuntimeError("BACKEND_PORT must be between 1 and 65535")

    return port


@dataclass(frozen=True, slots=True)
class IssuerSettings:
    bot_secret: str = field(repr=False)
    backend_secret: str = field(repr=False)
    ttl_seconds: int

    @classmethod
    def from_environment(cls) -> IssuerSettings:
        bot_secret = _read_secret("TOKEN_BOT_CLIENT_SECRET")
        backend_secret = _read_secret("TOKEN_BACKEND_CLIENT_SECRET")
        if bot_secret == backend_secret:
            raise RuntimeError("Token issuer clients must have distinct secrets")

        try:
            ttl_seconds = int(os.environ.get("TOKEN_TTL_SECONDS", "900"))
        except ValueError:
            raise RuntimeError("TOKEN_TTL_SECONDS must be an integer") from None
        if not 1 <= ttl_seconds <= 3600:
            raise RuntimeError("TOKEN_TTL_SECONDS must be between 1 and 3600")

        return cls(bot_secret=bot_secret, backend_secret=backend_secret, ttl_seconds=ttl_seconds)


def _read_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if re.fullmatch(r"[A-Za-z0-9_-]{32,}", value) is None:
        raise RuntimeError(f"{name} must be a URL-safe secret of at least 32 characters")
    return value
