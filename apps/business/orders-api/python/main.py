"""Orders REST API — the deployable app (ADR-0022).

Standard three-module app layout: settings.py (env mapping), dependencies.py (DI wiring +
the FastAPI dependency accessors), and this main.py (the app factory, lifespan, and
middleware). Routes live under routes/. The lifespan builds the Temporal client via
appkit (data-converter contract baked in) and wraps the domain TemporalService around it.
Run with: uvicorn main:app
"""

import os
import socket
import uuid
from contextlib import asynccontextmanager

from appkit import build_temporal_client
from dependencies import container
from fastapi import FastAPI, Request
from obslog import bound
from orders.db.models import Base
from orders.services.temporal import TemporalService
from routes import admin, internal, orders
from settings import settings


# --- App Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Resource identity for the log/telemetry schema (service name is the settings
    # default "orders-service"; instance = pod name / hostname).
    container.config.service_instance_id.override(
        os.getenv("HOSTNAME") or socket.gethostname()
    )
    # Start telemetry (OTel providers + Prometheus metrics endpoint + obslog).
    # Not awaited: the telemetry resource is a sync generator.
    container.init_resources()
    telemetry = container.telemetry()

    # Initialize DB schema
    db = container.database()
    async with db._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Build the Temporal client (carries the data-converter contract + the OTel
    # Runtime / TracingInterceptor) and wrap the domain service around it.
    client = await build_temporal_client(
        address=settings.temporal_address,
        namespace=settings.temporal_namespace,
        runtime=telemetry.runtime,
        interceptors=telemetry.interceptors,
        tls=settings.temporal_tls,
        api_key=settings.temporal_api_key,
        tls_client_cert_path=settings.temporal_tls_client_cert_path,
        tls_client_key_path=settings.temporal_tls_client_key_path,
        tls_server_ca_cert_path=settings.temporal_tls_server_ca_cert_path,
    )
    app.state.temporal_service = TemporalService(client)

    yield

    await db.disconnect()
    # Flush in-flight spans / logs / metrics before the process exits.
    container.shutdown_resources()


app = FastAPI(title="Orders Service", lifespan=lifespan)


@app.middleware("http")
async def bind_request_context(request: Request, call_next):
    """Bind a per-request id + route into the log context (concurrency-safe via
    contextvars) so every log emitted while handling the request — including
    library logs — carries it without threading it through each call."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    with bound(request_id=request_id, method=request.method, path=request.url.path):
        response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(orders.router)
app.include_router(internal.router)
app.include_router(admin.router)
