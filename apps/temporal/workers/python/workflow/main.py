"""Orders workflow worker — the deployable app (ADR-0022).

One worker profile per directory; this is the workflow worker. Its composition root
wires only what it needs: telemetry and a Temporal client (built by appkit, with the
data-converter contract baked in). It hosts the OrderWorkflow and no activities, so it
carries no mock-api / orders-service ports — each worker reasons about what it truly needs.
Run with: python main.py
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
from orders.shared.temporal_ids import TaskQueue
from orders.workflows.order_workflow import OrderWorkflow


class Settings(TemporalConnectionSettings, WorkerTuningSettings, TelemetrySettings):
    temporal_namespace: str = "ziggymart"
    otel_service_name: str = "orders-worker-workflow"


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


container = Container()
container.config.from_pydantic(settings)


async def main() -> None:
    # Resource identity for the log/telemetry schema: instance = pod name (HOSTNAME
    # in k8s), version = Worker Build ID when versioning is on.
    container.config.service_instance_id.override(
        os.getenv("HOSTNAME") or socket.gethostname()
    )
    if os.getenv("TEMPORAL_WORKER_BUILD_ID"):
        container.config.worker_build_id.override(
            os.environ["TEMPORAL_WORKER_BUILD_ID"]
        )
    # Start telemetry (OTel providers + Prometheus metrics endpoint + obslog).
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

    await run_worker(
        client,
        task_queue=TaskQueue.ORDERS_WORKFLOW,
        workflows=[OrderWorkflow],
        activities=[],
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
