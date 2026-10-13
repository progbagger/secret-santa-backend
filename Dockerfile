FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM python:3.14-slim AS builder

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --group migration --no-editable

FROM python:3.14-slim AS runtime

RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic ./alembic
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

CMD ["secret-santa-backend"]
