"""Dependency providers for the orders workflow worker — the composition root (ADR-0022).

Wires only what this worker needs: telemetry (a Resource — owns init/shutdown). The
Temporal client is async to construct, so it is built in main.py from
appkit.build_temporal_client (data-converter contract baked in). This worker hosts the
OrderWorkflow and no activities, so it wires no mock-api / orders-service ports.
"""

from appkit import Telemetry, telemetry_resource
from dependency_injector import containers, providers
from settings import settings


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()

    telemetry: providers.Resource[Telemetry] = providers.Resource(
        telemetry_resource,
        service_name=config.otel_service_name,
        otlp_endpoint=config.otel_exporter_otlp_endpoint,
        metrics_otlp_endpoint=config.otel_exporter_otlp_metrics_endpoint,
        sdk_metrics_port=config.sdk_metrics_port,
        log_level=config.log_level,
        log_format=config.log_format,
        log_otlp_push=config.log_otlp_push,
        namespace=config.service_namespace,
        instance_id=config.service_instance_id,
        version=config.worker_build_id,
    )


container = Container()
container.config.from_pydantic(settings)
