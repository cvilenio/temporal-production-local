import asyncio
from contextlib import asynccontextmanager

from app import db
from app.routes import orders_api, pages, status_api, tracking_api
from app.services.docker_status import poll_status_loop
from app.sse import poll_order_updates
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB connection pool
    await db.init_db()

    # Start the SSE background poller
    poller_task = asyncio.create_task(poll_order_updates())

    # Start Docker status poller
    status_poller_task = asyncio.create_task(poll_status_loop())

    yield

    # Cleanup
    for task in (poller_task, status_poller_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await db.close_db()


app = FastAPI(title="Retail Demo Console", lifespan=lifespan)

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
