"""Composition root for the orders activity worker (ADR-0022).

Wires the ports this worker's activities need — the mock-api client and the orders-service
client (both Singletons) — plus telemetry (a Resource — owns init/shutdown). The Temporal
client is async to construct, so it is built in main.py from appkit.build_temporal_client
(data-converter contract baked in). This worker hosts no workflow.
"""

from appkit import Telemetry, telemetry_resource
from dependency_injector import containers, providers
from orders.clients.mock_api import MockApiClient
from orders.clients.orders_service import OrdersServiceClient
from settings import settings


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()

    telemetry: providers.Resource[Telemetry] = providers.Resource(
        telemetry_resource,
        service_name=config.otel_service_name,
        otlp_endpoint=config.otel_exporter_otlp_endpoint,
        sdk_metrics_port=config.sdk_metrics_port,
        log_level=config.log_level,
        log_format=config.log_format,
        log_otlp_push=config.log_otlp_push,
        namespace=config.service_namespace,
        instance_id=config.service_instance_id,
        version=config.worker_build_id,
    )

    mock_api = providers.Singleton(MockApiClient, base_url=config.mock_api_url)
    orders_service_client = providers.Singleton(
        OrdersServiceClient, base_url=config.orders_service_url
    )


container = Container()
container.config.from_pydantic(settings)
