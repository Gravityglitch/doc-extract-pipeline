# syntax=docker/dockerfile:1

# ---- Builder: resolve and install the locked environment with uv ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install third-party dependencies first so this layer caches across source edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the project and install it into the same venv.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini README.md ./
RUN uv sync --frozen --no-dev


# ---- Runtime: slim image with only the app and its virtualenv ----
FROM python:3.12-slim-bookworm

# Unprivileged user for running the pipeline.
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

# Sample data and fixtures are needed for `docpipe run --mock`.
COPY --chown=app:app data/ ./data/
COPY --chown=app:app tests/fixtures/ ./tests/fixtures/

ENV PATH="/app/.venv/bin:$PATH"

USER app

EXPOSE 8000

CMD ["docpipe", "run"]
