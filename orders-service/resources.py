from functools import lru_cache
from config import settings
from db.engine import Database
from services.temporal import TemporalService
from clients.mock_api import MockApiClient
from clients.orders_service import OrdersServiceClient

@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database(db_url=settings.database_url)

@lru_cache(maxsize=1)
def get_temporal_service() -> TemporalService:
    return TemporalService(
        temporal_address=settings.temporal_address,
        temporal_namespace=settings.temporal_namespace,
    )

@lru_cache(maxsize=1)
def get_mock_api() -> MockApiClient:
    return MockApiClient(base_url=settings.mock_api_url)

@lru_cache(maxsize=1)
def get_orders_service_client() -> OrdersServiceClient:
    return OrdersServiceClient(base_url=settings.orders_service_url)
