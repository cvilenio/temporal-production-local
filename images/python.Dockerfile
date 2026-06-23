# syntax=docker/dockerfile:1.7
# =============================================================================
# Configurable image for any Python app in this monorepo.
#
# Each app is a THIN definition; build args select which uv dependency group to
# install and which entrypoint to run. The shared kernel (libs/) is always
# copied so the uv workspace resolves and kernel-based apps can import
# `orders_kernel`. Apps that don't use the kernel (console, mock-api) simply
# don't pull it into their group.
#
#   APP_GROUP   uv dependency group: workers | orders-api | codec-server
#                                    | retail-demo-console | mock-api
#   APP_PATH    app dir, e.g. apps/business/orders-api/python, apps/temporal/workers/python/workflow
#   APP_MODULE  uvicorn target (main:app) or python module; workers override CMD
#   APP_CMD     uvicorn | python
#   APP_PORT    listen port (web apps only)
# =============================================================================
FROM python:3.12-slim

ARG APP_GROUP
ARG APP_PATH
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

# Workspace manifests + shared kernel source (a workspace member).
COPY pyproject.toml uv.lock .python-version ./
COPY libs ./libs

# Install only this app's dependency group. Kernel-based groups (workers,
# orders-api) pull `orders-kernel`; others install just their own deps.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --group ${APP_GROUP}

# Copy the thin app definition last so dependency layers stay cached.
COPY ${APP_PATH} ./app

WORKDIR /app/app
ENV APP_MODULE=${APP_MODULE} APP_PORT=${APP_PORT} APP_CMD=${APP_CMD}
EXPOSE ${APP_PORT}

# Web apps use this; worker services override `command:` with `python main.py`.
CMD ${APP_CMD} ${APP_MODULE} --host 0.0.0.0 --port ${APP_PORT}
