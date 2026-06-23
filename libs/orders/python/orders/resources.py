"""
Dependency accessors — thin wrappers around the Container's providers.

Used by FastAPI Depends() and by worker.py to get concrete instances.
No wiring / @inject / Provide[] is used; resolution is explicit.

Lifecycle rule
--------------
Each process entrypoint must override the service name and call
`container.init_resources()` (NOT awaited — telemetry is a sync resource) before
any accessor that depends on telemetry (get_temporal_service). Teardown calls
`container.shutdown_resources()` to flush telemetry.
"""

from orders.clients.mock_api import MockApiClient
from orders.clients.orders_service import OrdersServiceClient
from orders.config import settings
from orders.containers import Container
from orders.db.engine import Database
from orders.services.temporal import TemporalService

container = Container()
container.config.from_pydantic(settings)


def get_database() -> Database:
    return container.database()


def get_temporal_service() -> TemporalService:
    return container.temporal_service()


def get_mock_api() -> MockApiClient:
    return container.mock_api()


def get_orders_service_client() -> OrdersServiceClient:
    return container.orders_service_client()
