"""Reusable settings field-groups (ADR-0022, class 3a).

Mixins an app composes (by multiple inheritance) with its own domain deltas. Each group
is generic — the *names* of the fields are app-agnostic and the defaults are neutral; an
app overrides any default it wants domain-specific (e.g. `temporal_namespace`) and adds
only the fields it actually uses, so an app that never calls a downstream service doesn't
carry that service's URL.
"""

from pydantic_settings import BaseSettings


class TemporalConnectionSettings(BaseSettings):
    """Temporal connection profile (ADR-0005).

    Local (Docker Compose or self-hosted on kind): TLS off, no auth (defaults).
    Temporal Cloud: set temporal_tls=true and supply either an API key
    (temporal_api_key) or mTLS client cert/key paths. The address becomes the
    Cloud endpoint, e.g. <namespace>.<account>.tmprl.cloud:7233.
    """

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_tls: bool = False
    temporal_api_key: str | None = None
    temporal_tls_client_cert_path: str | None = None
    temporal_tls_client_key_path: str | None = None


class WorkerTuningSettings(BaseSettings):
    """Worker slot / concurrency tuning knobs (temporal-workertuning)."""

    worker_max_concurrent_activities: int = 200
    worker_max_concurrent_workflow_tasks: int = 200
    worker_max_concurrent_local_activities: int = 200
    worker_max_concurrent_activity_task_polls: int = 10
    worker_max_concurrent_workflow_task_polls: int = 10
    worker_max_cached_workflows: int = 10_000


class TelemetrySettings(BaseSettings):
    """Observability + structured-logging settings (ADR-0018).

    Each process overrides otel_service_name at startup before telemetry init.
    log_otlp_push: push logs over OTLP directly to the backend. TRUE on the host
    plane (no node agent). FALSE on Kubernetes, where the Grafana Alloy DaemonSet
    tails pod stdout instead. service_namespace is the OTel service.namespace (the
    domain); service_instance_id is the OTel service.instance.id (pod name / hostname).
    """

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    # Optional separate endpoint for OTLP push business metrics (ADR-0024). When
    # unset, business metrics push to otel_exporter_otlp_endpoint (the trace
    # endpoint). Set this to the standalone OTel Collector so business metrics land
    # in ClickHouse while traces stay on lgtm/Tempo.
    otel_exporter_otlp_metrics_endpoint: str | None = None
    otel_service_name: str = "app"
    # SDK operational metrics (Temporal runtime) bind a Prometheus /metrics pull
    # endpoint on this port inside the container (scraped by Prometheus in lgtm).
    sdk_metrics_port: int = 9000

    log_level: str = "INFO"
    log_format: str = "json"
    log_otlp_push: bool = True
    service_namespace: str | None = None
    service_instance_id: str | None = None
    worker_build_id: str | None = None
