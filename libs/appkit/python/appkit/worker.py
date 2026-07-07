"""Generic worker-from-profile loop + deployment-config builder (ADR-0022, class 3a).

Names no workflow or activity. A `WorkerProfile` is a data shape (task queue + the
workflows and activity-group names a deployable hosts); resolving group names to concrete
activity callables is the domain's job. `run_worker(...)` builds the `Worker`, runs it, and
calls the caller's `on_shutdown` in `finally` — the app owns the telemetry lifecycle; the
kit owns the error-prone construction.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from obslog import get_logger
from temporalio.worker import Worker

if TYPE_CHECKING:
    from temporalio.client import Client
    from temporalio.worker import WorkerDeploymentConfig

log = get_logger(__name__)


@dataclass(frozen=True)
class WorkerProfile:
    """One deployable worker.

    Scaling out the fleet is additive: register another profile and add a matching
    thin entrypoint under apps/.../<name>/. That is how a CPU-bound activity worker
    lives alongside an IO-bound one — separate profiles, separate task queues, scaled
    and tuned independently. `activity_groups` are opaque names the domain resolves
    to concrete activity callables.
    """

    name: str
    task_queue: str
    workflows: list = field(default_factory=list)
    activity_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkerTuning:
    """Slot / concurrency / poller knobs passed to the SDK `Worker`."""

    max_concurrent_activities: int = 200
    max_concurrent_workflow_tasks: int = 200
    max_concurrent_local_activities: int = 200
    max_concurrent_activity_task_polls: int = 10
    max_concurrent_workflow_task_polls: int = 10
    max_cached_workflows: int = 10_000


def build_deployment_config(
    *, default_deployment_name: str = "default"
) -> WorkerDeploymentConfig | None:
    """Build a WorkerDeploymentConfig from env when Worker Versioning is enabled.

    Returns None unless TEMPORAL_WORKER_BUILD_ID is set. In Kubernetes the Temporal
    Worker Controller injects TEMPORAL_DEPLOYMENT_NAME and TEMPORAL_WORKER_BUILD_ID
    (the Build ID is derived from the pod-template hash, so shipping a new version is
    just a new image tag). Local/compose runs leave both unset and stay
    version-agnostic, preserving prior behavior. (ADR-0004.)
    """
    build_id = os.getenv("TEMPORAL_WORKER_BUILD_ID")
    if not build_id:
        return None
    from temporalio.common import WorkerDeploymentVersion
    from temporalio.worker import WorkerDeploymentConfig

    deployment_name = os.getenv("TEMPORAL_DEPLOYMENT_NAME") or default_deployment_name
    return WorkerDeploymentConfig(
        version=WorkerDeploymentVersion(
            deployment_name=deployment_name, build_id=build_id
        ),
        use_worker_versioning=True,
    )


async def run_worker(
    client: Client,
    *,
    task_queue: str,
    workflows: list,
    activities: list,
    tuning: WorkerTuning,
    deployment_config: WorkerDeploymentConfig | None = None,
    on_shutdown: Callable[[], object] | None = None,
) -> None:
    """Build and run a Temporal Worker, draining telemetry on exit.

    `on_shutdown` runs in `finally` — on SIGTERM the SDK handles the signal and returns
    from worker.run(), then this flushes the last telemetry batch (the app's concern).
    """
    log.info(
        "starting Temporal worker",
        task_queue=str(task_queue),
        versioning="on" if deployment_config else "off",
        concurrency={
            "activities": tuning.max_concurrent_activities,
            "workflow_tasks": tuning.max_concurrent_workflow_tasks,
            "local_activities": tuning.max_concurrent_local_activities,
            "activity_polls": tuning.max_concurrent_activity_task_polls,
            "workflow_polls": tuning.max_concurrent_workflow_task_polls,
            "cached_workflows": tuning.max_cached_workflows,
        },
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
        deployment_config=deployment_config,
        max_concurrent_activities=tuning.max_concurrent_activities,
        max_concurrent_workflow_tasks=tuning.max_concurrent_workflow_tasks,
        max_concurrent_local_activities=tuning.max_concurrent_local_activities,
        max_concurrent_activity_task_polls=tuning.max_concurrent_activity_task_polls,
        max_concurrent_workflow_task_polls=tuning.max_concurrent_workflow_task_polls,
        max_cached_workflows=tuning.max_cached_workflows,
    )

    try:
        await worker.run()
    finally:
        if on_shutdown is not None:
            on_shutdown()
