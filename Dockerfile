# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ARG APP
ARG APP_MODULE
ARG APP_PORT=8000
ARG APP_CMD=uvicorn

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# Install only the target app's dependency group.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --only-group ${APP}

# Copy the target app's source only.
COPY ${APP} ./${APP}

# Working dir becomes the app dir so relative imports keep working.
WORKDIR /app/${APP}

ENV APP_MODULE=${APP_MODULE} APP_PORT=${APP_PORT} APP_CMD=${APP_CMD}
EXPOSE ${APP_PORT}

# Shell form so env vars expand at runtime.
CMD ${APP_CMD} ${APP_MODULE} --host 0.0.0.0 --port ${APP_PORT}