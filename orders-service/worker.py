import asyncio
import argparse
import os

from temporalio.worker import Worker

from activities import (
    make_external_activities,
    make_persistence_activities,
    make_customer_message_activities,
)
from workflows.order_workflow import OrderWorkflow
from config import settings
from shared.temporal_ids import TaskQueue
from resources import container, get_temporal_service, get_mock_api, get_orders_service_client


async def main():
    parser = argparse.ArgumentParser(description="Run Temporal Worker")
    parser.add_argument(
        "--role",
        type=str,
        choices=["workflow", "activity"],
        default=os.getenv("WORKER_ROLE"),
        required=os.getenv("WORKER_ROLE") is None,
        help="Role of the worker (workflow or activity)",
    )
    args = parser.parse_args()

    # Per-process service name — must override before init_resources so the
    # telemetry resource initialises with the correct service name.
    container.config.otel_service_name.override(f"orders-worker-{args.role}")
    # Start telemetry (OTel providers + Prometheus metrics endpoint).
    # Not awaited: the telemetry resource is a sync generator.
    container.init_resources()

    print(
        f"Connecting to Temporal at {settings.temporal_address} (namespace: {settings.temporal_namespace})..."
    )

    temporal_service = get_temporal_service()
    client = await temporal_service.connect()

    if args.role == "workflow":
        task_queue = TaskQueue.ORDERS_WORKFLOW
        workflows = [OrderWorkflow]
        activities = []
    else:  # activity
        task_queue = TaskQueue.ORDERS_ACTIVITY
        workflows = []
        mock_api = get_mock_api()
        orders_client = get_orders_service_client()
        activities = [
            *make_external_activities(mock_api),
            *make_persistence_activities(orders_client),
            *make_customer_message_activities(orders_client),
        ]

    print(
        f"Starting Temporal Worker [ROLE: {args.role}] on queue [{task_queue}] with concurrency:\n"
        f"  activities={settings.worker_max_concurrent_activities}, "
        f"workflow_tasks={settings.worker_max_concurrent_workflow_tasks}, "
        f"local_activities={settings.worker_max_concurrent_local_activities}\n"
        f"  activity_polls={settings.worker_max_concurrent_activity_task_polls}, "
        f"workflow_polls={settings.worker_max_concurrent_workflow_task_polls}, "
        f"cached_workflows={settings.worker_max_cached_workflows}"
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
        max_concurrent_activities=settings.worker_max_concurrent_activities,
        max_concurrent_workflow_tasks=settings.worker_max_concurrent_workflow_tasks,
        max_concurrent_local_activities=settings.worker_max_concurrent_local_activities,
        max_concurrent_activity_task_polls=settings.worker_max_concurrent_activity_task_polls,
        max_concurrent_workflow_task_polls=settings.worker_max_concurrent_workflow_task_polls,
        max_cached_workflows=settings.worker_max_cached_workflows,
    )

    try:
        await worker.run()
    finally:
        # Flush in-flight spans / logs / metrics before the process exits.
        # Runs on SIGTERM — Temporal's worker handles the signal and returns
        # from worker.run(), then this drains the last telemetry batch before
        # Docker Compose removes the container.
        container.shutdown_resources()


if __name__ == "__main__":
    asyncio.run(main())
