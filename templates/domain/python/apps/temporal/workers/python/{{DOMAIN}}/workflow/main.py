"""{{Domain}} workflow worker entrypoint."""

import asyncio
import os
import socket

from appkit import (
    WorkerTuning,
    build_deployment_config,
    build_temporal_client,
    data_converter_for_namespace,
    run_worker,
)
from {{DOMAIN}}.shared.temporal_ids import TaskQueue
from {{DOMAIN}}.workflows.hello_workflow import HelloWorkflow
from dependencies import container
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
        tls_server_ca_cert_path=settings.temporal_tls_server_ca_cert_path,
        data_converter=data_converter_for_namespace(settings.temporal_namespace),
    )

    await run_worker(
        client,
        task_queue=TaskQueue.WORKFLOW,
        workflows=[HelloWorkflow],
        activities=[],
        tuning=WorkerTuning(
            max_concurrent_activities=settings.worker_max_concurrent_activities,
            max_concurrent_workflow_tasks=settings.worker_max_concurrent_workflow_tasks,
            max_concurrent_local_activities=settings.worker_max_concurrent_local_activities,
            max_concurrent_activity_task_polls=settings.worker_max_concurrent_activity_task_polls,
            max_concurrent_workflow_task_polls=settings.worker_max_concurrent_workflow_task_polls,
            max_cached_workflows=settings.worker_max_cached_workflows,
        ),
        deployment_config=build_deployment_config(
            default_deployment_name="{{DOMAIN}}-workflow"
        ),
        on_shutdown=container.shutdown_resources,
    )


if __name__ == "__main__":
    asyncio.run(main())
