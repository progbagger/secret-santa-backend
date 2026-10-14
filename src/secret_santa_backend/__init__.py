import uvicorn

from secret_santa_backend.config import Settings


def main() -> None:
    settings = Settings.from_environment()
    uvicorn.run(
        "secret_santa_backend.web.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )
