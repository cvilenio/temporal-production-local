"""Orders activity worker (IO-bound side-effects) — the deployable app (ADR-0022).

One worker profile per directory; this is the activity worker. Its composition root wires
the ports its activities need: the mock-api client and the orders-service client (plus
telemetry and the Temporal client). It hosts no workflow. To add a CPU-bound activity
worker, add a sibling apps/temporal/workers/python/activity-cpu/ that wires its own ports
and a different task queue. Run with: python main.py
"""

import asyncio
import os
import socket

from appkit import (
    Telemetry,
    TelemetrySettings,
    TemporalConnectionSettings,
    WorkerTuning,
    WorkerTuningSettings,
    build_deployment_config,
    build_temporal_client,
    run_worker,
    telemetry_resource,
)
from dependency_injector import containers, providers
from orders.activities import (
    make_customer_message_activities,
    make_external_activities,
    make_persistence_activities,
)
from orders.clients.mock_api import MockApiClient
from orders.clients.orders_service import OrdersServiceClient
from orders.shared.temporal_ids import TaskQueue


class Settings(TemporalConnectionSettings, WorkerTuningSettings, TelemetrySettings):
    temporal_namespace: str = "ziggymart"
    otel_service_name: str = "orders-worker-activity"

    # Ports this worker's activities call.
    mock_api_url: str = "http://localhost:8001"
    orders_service_url: str = "http://localhost:8002"


settings = Settings()


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


async def main() -> None:
    container.config.service_instance_id.override(
        os.getenv("HOSTNAME") or socket.gethostname()
    )
    if os.getenv("TEMPORAL_WORKER_BUILD_ID"):
        container.config.worker_build_id.override(
            os.environ["TEMPORAL_WORKER_BUILD_ID"]
        )
    container.init_resources()
    telemetry = container.telemetry()

    client = await build_temporal_client(
        address=settings.temporal_address,
        namespace=settings.temporal_namespace,
        runtime=telemetry.runtime,
        interceptors=telemetry.interceptors,
        tls=settings.temporal_tls,
        api_key=settings.temporal_api_key,
        tls_client_cert_path=settings.temporal_tls_client_cert_path,
        tls_client_key_path=settings.temporal_tls_client_key_path,
    )

    mock_api = container.mock_api()
    orders_service = container.orders_service_client()
    activities: list = [
        *make_external_activities(mock_api),
        *make_persistence_activities(orders_service),
        *make_customer_message_activities(orders_service),
    ]

    await run_worker(
        client,
        task_queue=TaskQueue.ORDERS_ACTIVITY,
        workflows=[],
        activities=activities,
        tuning=WorkerTuning(
            max_concurrent_activities=settings.worker_max_concurrent_activities,
            max_concurrent_workflow_tasks=settings.worker_max_concurrent_workflow_tasks,
            max_concurrent_local_activities=settings.worker_max_concurrent_local_activities,
            max_concurrent_activity_task_polls=settings.worker_max_concurrent_activity_task_polls,
            max_concurrent_workflow_task_polls=settings.worker_max_concurrent_workflow_task_polls,
            max_cached_workflows=settings.worker_max_cached_workflows,
        ),
        deployment_config=build_deployment_config(default_deployment_name="orders"),
        on_shutdown=container.shutdown_resources,
    )


if __name__ == "__main__":
    asyncio.run(main())
