import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Literal
from uuid import uuid4

import aiohttp
import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yml"
TOKEN_ENV = {
    "TOKEN_BOT_CLIENT_SECRET": "test-bot-secret-not-for-production",
    "TOKEN_BACKEND_CLIENT_SECRET": "test-backend-secret-not-for-production",
    "TOKEN_TTL_SECONDS": "900",
}


@dataclass(slots=True)
class BackendEnvironment:
    work_directory: Path
    postgres_port: int = field(default_factory=lambda: _free_port())
    issuer_port: int = field(default_factory=lambda: _free_port())
    backend_port: int = field(default_factory=lambda: _free_port())
    project_name: str = field(default_factory=lambda: f"ss-test-{uuid4().hex[:10]}")
    process: subprocess.Popen[bytes] | None = None
    issuer_process: subprocess.Popen[bytes] | None = None
    authorization: dict[str, str] = field(default_factory=dict)
    _log_file: IO[bytes] | None = None

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.backend_port}"

    @property
    def issuer_url(self) -> str:
        return f"http://127.0.0.1:{self.issuer_port}"

    async def request_token(
        self, *, role: str = "bot", secret: str = TOKEN_ENV["TOKEN_BOT_CLIENT_SECRET"]
    ) -> aiohttp.ClientResponse:
        async with (
            aiohttp.ClientSession(base_url=self.issuer_url) as client,
            client.post(
                "/token",
                headers={"Authorization": aiohttp.encode_basic_auth(role, secret)},
                data={"grant_type": "client_credentials"},
            ) as response,
        ):
            await response.read()
            return response

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://secret_santa:secret_santa@127.0.0.1:{self.postgres_port}/secret_santa"

    @property
    def asyncpg_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def start_stack(self) -> None:
        self._run_compose("up", "-d", "--build", "backend")
        asyncio.run(self._wait_for_stack())

    async def _wait_for_stack(self) -> None:
        await self.wait_for_http("/health/ready", expected_status=200, timeout=90)
        response = await self.request_token()
        response.raise_for_status()
        token = (await response.json())["access_token"]
        self.authorization = {"Authorization": f"Bearer {token}"}

    def start_postgres(self) -> None:
        self._run_compose("up", "-d", "postgres")
        self._wait_for_postgres()

    def migrate(self, revision: str = "head", *, direction: Literal["upgrade", "downgrade"] = "upgrade") -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", direction, revision],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": self.database_url},
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Alembic migration failed:\n"
                f"stdout:\n{result.stdout.decode(errors='replace')}\n"
                f"stderr:\n{result.stderr.decode(errors='replace')}"
            )

    def set_issuer_time(self, now: datetime) -> None:
        clock_file = self.work_directory / "issuer-clock"
        pending = clock_file.with_suffix(".tmp")
        pending.write_text(now.isoformat())
        pending.replace(clock_file)

    def start_local_issuer(self) -> None:
        clock_file = self.work_directory / "issuer-clock"
        if not clock_file.exists():
            self.set_issuer_time(datetime.now(UTC))

        with (self.work_directory / "issuer.log").open("wb") as output:
            self.issuer_process = subprocess.Popen(
                [sys.executable, "-m", "tests.functional.run_token_issuer", str(self.issuer_port), str(clock_file)],
                cwd=ROOT,
                env={**os.environ, **TOKEN_ENV},
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        asyncio.run(self.wait_for_http("/health/ready", expected_status=200, timeout=20, issuer=True))

    def stop_local_issuer(self) -> None:
        if self.issuer_process is not None:
            _stop_process(self.issuer_process)
            self.issuer_process = None

    def start_local_backend(self) -> None:
        log_path = self.work_directory / "backend.log"
        self._log_file = log_path.open("w+b")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "secret_santa_backend"],
            cwd=ROOT,
            env={
                **os.environ,
                "DATABASE_URL": self.database_url,
                "TOKEN_ISSUER_URL": self.issuer_url,
                **TOKEN_ENV,
                "BACKEND_HOST": "127.0.0.1",
                "BACKEND_PORT": str(self.backend_port),
                "PYTHONUNBUFFERED": "1",
            },
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        asyncio.run(self.wait_for_http("/health/live", expected_status=200, timeout=20))

    async def wait_for_http(
        self, path: str, *, expected_status: int, timeout: float, issuer: bool = False
    ) -> aiohttp.ClientResponse:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None

        base_url = self.issuer_url if issuer else self.api_url
        process = self.issuer_process if issuer else self.process
        async with aiohttp.ClientSession(base_url=base_url, timeout=aiohttp.ClientTimeout(total=1)) as client:
            while time.monotonic() < deadline:
                if process is not None and process.poll() is not None:
                    log = self._read_issuer_log() if issuer else self._read_backend_log()
                    raise RuntimeError(f"Server exited unexpectedly:\n{log}")

                try:
                    async with client.get(path) as response:
                        if response.status == expected_status:
                            await response.read()
                            return response
                except (aiohttp.ClientError, TimeoutError) as error:
                    last_error = error

                await asyncio.sleep(0.1)

        diagnostics = self._compose_logs()
        raise RuntimeError(
            f"Timed out waiting for {path} to return {expected_status}; last_error={last_error!r}\n"
            f"backend log:\n{self._read_backend_log()}\nissuer log:\n{self._read_issuer_log()}\n"
            f"compose logs:\n{diagnostics}"
        )

    def close(self) -> None:
        if self.process is not None:
            _stop_process(self.process)
        self.stop_local_issuer()

        if self._log_file is not None:
            self._log_file.close()

        self._run_compose("down", "--volumes", "--remove-orphans", check=False)

    def _wait_for_postgres(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if asyncio.run(_can_connect_to_postgres(self.asyncpg_url)):
                return

            time.sleep(0.1)

        raise RuntimeError(f"PostgreSQL did not become ready:\n{self._compose_logs()}")

    def _run_compose(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                self.project_name,
                "--file",
                str(COMPOSE_FILE),
                *arguments,
            ],
            cwd=ROOT,
            env={
                **os.environ,
                **TOKEN_ENV,
                "TOKEN_ISSUER_URL": "http://token-issuer:8000",
                "TOKEN_ISSUER_PORT": str(self.issuer_port),
                "POSTGRES_PORT": str(self.postgres_port),
                "BACKEND_PORT": str(self.backend_port),
            },
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Docker Compose command failed: {' '.join(arguments)}\n"
                f"stdout:\n{result.stdout.decode(errors='replace')}\n"
                f"stderr:\n{result.stderr.decode(errors='replace')}"
            )

        return result

    def _read_backend_log(self) -> str:
        log_path = self.work_directory / "backend.log"
        if not log_path.exists():
            return ""
        return log_path.read_text(errors="replace")

    def _read_issuer_log(self) -> str:
        log_path = self.work_directory / "issuer.log"
        return log_path.read_text(errors="replace") if log_path.exists() else ""

    def _compose_logs(self) -> str:
        result = self._run_compose("logs", "--no-color", check=False)
        return (result.stdout + result.stderr).decode(errors="replace")


@pytest.fixture(scope="module")
def running_backend(tmp_path_factory: pytest.TempPathFactory) -> Iterator[BackendEnvironment]:
    environment = BackendEnvironment(tmp_path_factory.mktemp("running-backend"))
    try:
        environment.start_stack()
        yield environment
    finally:
        environment.close()


@pytest.fixture
def backend_without_postgres(tmp_path: Path) -> Iterator[BackendEnvironment]:
    environment = BackendEnvironment(tmp_path)
    try:
        environment.start_local_issuer()
        environment.start_local_backend()
        yield environment
    finally:
        environment.close()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


async def _can_connect_to_postgres(database_url: str) -> bool:
    try:
        connection = await asyncpg.connect(database_url, timeout=1)
    except asyncpg.PostgresError, OSError, TimeoutError:
        return False

    await connection.close()
    return True
