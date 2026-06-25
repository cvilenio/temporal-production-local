import asyncio
from contextlib import asynccontextmanager

from app import db
from app.routes import orders_api, pages, status_api, tracking_api
from app.services.status import poll_status_loop
from app.sse import poll_order_updates
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort DB connect — never fatal. The console must boot and serve even
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
app.include_router(tracking_api.router)
app.include_router(status_api.router)


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}
