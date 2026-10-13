import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO
from uuid import uuid4

import asyncpg
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yml"


@dataclass(slots=True)
class BackendEnvironment:
    work_directory: Path
    postgres_port: int = field(default_factory=lambda: _free_port())
    backend_port: int = field(default_factory=lambda: _free_port())
    project_name: str = field(default_factory=lambda: f"ss-test-{uuid4().hex[:10]}")
    process: subprocess.Popen[bytes] | None = None
    _log_file: IO[bytes] | None = None

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.backend_port}"

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://secret_santa:secret_santa@127.0.0.1:{self.postgres_port}/secret_santa"

    @property
    def asyncpg_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def start_stack(self) -> None:
        self._run_compose("up", "-d", "--build", "backend")
        self.wait_for_http("/health/ready", expected_status=200, timeout=90)

    def start_postgres(self) -> None:
        self._run_compose("up", "-d", "postgres")
        self._wait_for_postgres()

    def migrate(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
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

    def start_local_backend(self) -> None:
        log_path = self.work_directory / "backend.log"
        self._log_file = log_path.open("w+b")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "secret_santa_backend"],
            cwd=ROOT,
            env={
                **os.environ,
                "DATABASE_URL": self.database_url,
                "BACKEND_HOST": "127.0.0.1",
                "BACKEND_PORT": str(self.backend_port),
                "PYTHONUNBUFFERED": "1",
            },
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        self.wait_for_http("/health/live", expected_status=200, timeout=20)

    def wait_for_http(self, path: str, *, expected_status: int, timeout: float) -> httpx.Response:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None

        with httpx.Client(timeout=1) as client:
            while time.monotonic() < deadline:
                if self.process is not None and self.process.poll() is not None:
                    raise RuntimeError(f"Backend exited unexpectedly:\n{self._read_backend_log()}")

                try:
                    response = client.get(f"{self.api_url}{path}")
                    if response.status_code == expected_status:
                        return response
                except httpx.HTTPError as error:
                    last_error = error

                time.sleep(0.1)

        diagnostics = self._compose_logs()
        raise RuntimeError(
            f"Timed out waiting for {path} to return {expected_status}; last_error={last_error!r}\n"
            f"backend log:\n{self._read_backend_log()}\ncompose logs:\n{diagnostics}"
        )

    def close(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

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
        environment.start_local_backend()
        yield environment
    finally:
        environment.close()


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
