"""Orders activity worker (IO-bound side-effects) — the deployable app (ADR-0022).

Standard three-module app layout: settings.py (env mapping), composition.py (DI wiring),
and this main.py (startup/lifecycle). One worker profile per directory; this is the
activity worker. It builds the Temporal client via appkit (data-converter contract baked
in), hosts the orders activities (no workflow), and owns the telemetry lifecycle. To add a
CPU-bound activity worker, add a sibling directory that wires its own ports and task queue.
Run with: python main.py
"""

import asyncio
import os
import socket

from appkit import (
    WorkerTuning,
    build_deployment_config,
    build_temporal_client,
    run_worker,
)
from composition import container
from orders.activities import (
    make_customer_message_activities,
    make_external_activities,
    make_persistence_activities,
)
from orders.shared.temporal_ids import TaskQueue
from settings import settings


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
