import asyncio
import os
import socket
from contextlib import asynccontextmanager

from app import db
from app.routes import domain_trigger_api, orders_api, pages, status_api, tracking_api
from app.services.status import poll_status_loop
from app.settings import settings
from app.sse import poll_order_updates
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from obslog import init_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Host-plane operability tooling: structured JSON to stdout (Docker Desktop).
    # No OTLP push — the console has no OTel dependency by design; its logs are
    # for the operator following a run in Docker Desktop, not Grafana. ADR-0018.
    init_logging(
        os.getenv("OTEL_SERVICE_NAME", "platform-console"),
        level=os.getenv("LOG_LEVEL", "INFO"),
        fmt=os.getenv("LOG_FORMAT", "json"),
        instance_id=os.getenv("HOSTNAME") or socket.gethostname(),
    )

    if settings.domain_descriptors_dir:
        os.environ["DOMAIN_DESCRIPTORS_DIR"] = settings.domain_descriptors_dir

    # Best-effort DB connect — never fatal.
    # with the whole kind side (orders-db, orders-api) unreachable; it's expected
    # to be running before the cluster exists. The maintainer establishes the
    # pool when the DB appears and re-establishes it if the DB goes away.
    await db.init_db()
    db_maintainer_task = asyncio.create_task(db.maintain_pool())

    # Start the SSE background poller
    poller_task = asyncio.create_task(poll_order_updates())

    # Start the substrate-aware live-status poller (Docker and/or Kube; ADR-0015)
    status_poller_task = asyncio.create_task(poll_status_loop())

    yield

    # Cleanup
    for task in (db_maintainer_task, poller_task, status_poller_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await db.close_db()


app = FastAPI(title="Platform Console", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(pages.router)
app.include_router(orders_api.router)
app.include_router(domain_trigger_api.router)
app.include_router(tracking_api.router)
app.include_router(status_api.router)


@app.get("/healthz")
def healthcheck():
    return {"status": "ok", "backend": settings.console_backend}
