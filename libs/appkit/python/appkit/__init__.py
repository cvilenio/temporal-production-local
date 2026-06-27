"""appkit — the generic application-composition kit (ADR-0022, class 3a).

Domain- *and* app-agnostic building blocks that every deployable shares: a Temporal
client builder (with the data-converter contract baked in), a SQL engine factory, the
telemetry bootstrap, the run-a-worker-from-profile loop, and reusable settings
field-groups. Nothing here names a workflow, activity, or external service of any
domain — that lives in `libs/<domain>`; assembly lives in `/apps`.
"""

from appkit.db import Database
from appkit.settings import (
    TelemetrySettings,
    TemporalConnectionSettings,
    WorkerTuningSettings,
)
from appkit.telemetry import Telemetry, init_observability, telemetry_resource
from appkit.temporal import build_temporal_client
from appkit.worker import (
    WorkerProfile,
    WorkerTuning,
    build_deployment_config,
    run_worker,
)

__all__ = [
    "Database",
    "Telemetry",
    "TelemetrySettings",
    "TemporalConnectionSettings",
    "WorkerProfile",
    "WorkerTuning",
    "WorkerTuningSettings",
    "build_deployment_config",
    "build_temporal_client",
    "init_observability",
    "run_worker",
    "telemetry_resource",
]
