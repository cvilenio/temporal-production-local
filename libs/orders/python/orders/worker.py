import argparse
import asyncio
import os
import socket

from appkit import WorkerProfile, WorkerTuning, build_deployment_config
from appkit import run_worker as _run_worker
from obslog import get_logger

from orders.activities import (
    make_customer_message_activities,
    make_external_activities,
    make_persistence_activities,
)
from orders.config import settings
from orders.resources import (
    container,
    get_mock_api,
    get_orders_service_client,
    get_temporal_service,
)
from orders.shared.temporal_ids import TaskQueue
from orders.workflows.order_workflow import OrderWorkflow

log = get_logger(__name__)


# ── Activity groups ──────────────────────────────────────────────────────────
# Named bundles of activity factories. A worker profile lists the groups it
# hosts. Splitting CPU-bound from IO-bound work is just a matter of putting the
# relevant activities in different groups on different task queues — see the
# extension note in WORKER_PROFILES below.
def _build_activity_group(name: str) -> list:
    if name == "external":
        return list(make_external_activities(get_mock_api()))
    if name == "persistence":
        return list(make_persistence_activities(get_orders_service_client()))
    if name == "customer_message":
        return list(make_customer_message_activities(get_orders_service_client()))
    raise ValueError(f"Unknown activity group: {name}")


# The worker fleet, keyed by profile name. Each thin app passes its own name.
WORKER_PROFILES: dict[str, WorkerProfile] = {
    "workflow": WorkerProfile(
        name="workflow",
        task_queue=TaskQueue.ORDERS_WORKFLOW,
        workflows=[OrderWorkflow],
    ),
    "activity": WorkerProfile(
        name="activity",
        task_queue=TaskQueue.ORDERS_ACTIVITY,
        activity_groups=("external", "persistence", "customer_message"),
    ),
    # Resource-profile split (when you want it): add ORDERS_ACTIVITY_IO /
    # ORDERS_ACTIVITY_CPU to TaskQueue, register e.g.
    #   "activity-io":  task_queue=ORDERS_ACTIVITY_IO,  groups ("external", "persistence")
    #   "activity-cpu": task_queue=ORDERS_ACTIVITY_CPU, groups ("customer_message",)
    # then route each activity to its queue in the workflow (task_queue=...).
}


async def run_worker(profile_name: str) -> None:
    """Run the worker described by the named profile.

    Thin app entrypoints under apps/workers/<lang>/<name>/ call this with a
    fixed profile name, so each deployment unit hosts exactly one worker. The
    generic build-and-run loop lives in appkit.run_worker; this resolves the
    orders profile (workflows + activity groups) and owns the telemetry lifecycle.
    """
    profile = WORKER_PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(
            f"Unknown worker profile '{profile_name}'. "
            f"Known profiles: {', '.join(sorted(WORKER_PROFILES))}"
        )

    # Per-process service name — must override before init_resources so the
    # telemetry resource initialises with the correct service name.
    container.config.otel_service_name.override(f"orders-worker-{profile.name}")
    # Resource identity for the log/telemetry schema: instance = pod name
    # (HOSTNAME in k8s), version = Worker Build ID when versioning is on.
    container.config.service_instance_id.override(
        os.getenv("HOSTNAME") or socket.gethostname()
    )
    if os.getenv("TEMPORAL_WORKER_BUILD_ID"):
        container.config.worker_build_id.override(
            os.environ["TEMPORAL_WORKER_BUILD_ID"]
        )
    # Start telemetry (OTel providers + Prometheus metrics endpoint + obslog).
    # Not awaited: the telemetry resource is a sync generator.
    container.init_resources()

    log.info(
        "connecting to Temporal",
        address=settings.temporal_address,
        namespace=settings.temporal_namespace,
        profile=profile.name,
    )

    temporal_service = get_temporal_service()
    client = await temporal_service.connect()

    activities: list = []
    for group in profile.activity_groups:
        activities.extend(_build_activity_group(group))

    await _run_worker(
        client,
        task_queue=profile.task_queue,
        workflows=profile.workflows,
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
        # Flush in-flight spans / logs / metrics before the process exits. Runs on
        # SIGTERM after worker.run() returns.
        on_shutdown=container.shutdown_resources,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Temporal worker by profile")
    parser.add_argument(
        "--profile",
        type=str,
        choices=sorted(WORKER_PROFILES),
        default=os.getenv("WORKER_PROFILE") or os.getenv("WORKER_ROLE"),
        required=(os.getenv("WORKER_PROFILE") or os.getenv("WORKER_ROLE")) is None,
        help="Worker profile to run",
    )
    args = parser.parse_args()
    await run_worker(args.profile)


if __name__ == "__main__":
    asyncio.run(main())
