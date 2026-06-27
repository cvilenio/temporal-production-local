"""Mock External Systems API — the deployable app (ADR-0022).

Standard three-module app layout: settings.py (env mapping), dependencies.py (the shared
in-process idempotency store + latency simulator the routes use), and this main.py
(app factory + lifespan). Routes live under routes/, grouped by system. Run with:
uvicorn main:app
"""

import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI
from obslog import get_logger, init_logging
from routes import inventory, payment, shipping
from settings import settings

log = get_logger("mock-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Host-plane business mock: emit JSON to stdout (Docker Desktop) AND push OTLP
    # straight to the observability backend (lgtm), since no node agent collects host
    # containers. On Kubernetes this would flip to stdout-only. See ADR-0018.
    handle = init_logging(
        settings.otel_service_name,
        level=settings.log_level,
        fmt=settings.log_format,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint
        if settings.log_otlp_push
        else None,
        namespace=settings.service_namespace,
        instance_id=os.getenv("HOSTNAME") or socket.gethostname(),
    )
    log.info("mock external systems API up")
    yield
    handle.shutdown()


app = FastAPI(title="Mock External Systems API", lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(payment.router)
app.include_router(inventory.router)
app.include_router(shipping.router)
