from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "ziggymart"

    # ── Temporal connection profile ───────────────────────────────────────────
    # Local (Docker Compose or self-hosted on kind): TLS off, no auth (defaults).
    # Temporal Cloud: set TEMPORAL_TLS=true and supply either an API key
    # (TEMPORAL_API_KEY) or mTLS client cert/key paths. The address becomes the
    # Cloud endpoint, e.g. <namespace>.<account>.tmprl.cloud:7233.
    temporal_tls: bool = False
    temporal_api_key: str | None = None
    temporal_tls_client_cert_path: str | None = None
    temporal_tls_client_key_path: str | None = None

    database_url: str = "postgresql+asyncpg://admin:password@localhost:5433/orders_db"
    mock_api_url: str = "http://localhost:8001"
    orders_service_url: str = "http://localhost:8002"

    # Demo-only kill switch for the destructive /admin/reset endpoint
    # (terminate workflows + truncate app tables). Defaults on for the local
    # demo; set DEMO_RESET_ENABLED=false to disable in any shared environment.
    demo_reset_enabled: bool = True

    # Worker slot / concurrency tuning
    worker_max_concurrent_activities: int = 200
    worker_max_concurrent_workflow_tasks: int = 200
    worker_max_concurrent_local_activities: int = 200
    worker_max_concurrent_activity_task_polls: int = 10
    worker_max_concurrent_workflow_task_polls: int = 10
    worker_max_cached_workflows: int = 10_000

    # Observability — set via env in Docker Compose; each process overrides
    # otel_service_name at startup before container.init_resources() is called.
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "orders-service"
    # SDK operational metrics (Temporal runtime) bind a Prometheus /metrics pull
    # endpoint on this port inside the container (scraped by Prometheus in lgtm).
    sdk_metrics_port: int = 9000

    # ── Logging (obslog) ──────────────────────────────────────────────────────
    # log_level   : root level for the structured logging pipeline.
    # log_format  : "json" (shipped/everywhere it's collected) or "console"
    #               (pretty local dev).
    # log_otlp_push: push logs over OTLP directly to the backend. TRUE on the
    #               host plane (no node agent). FALSE on Kubernetes, where the
    #               Grafana Alloy DaemonSet tails pod stdout instead (ADR-0018).
    # service_namespace: OTel service.namespace — the domain (e.g. "ziggymart").
    # service_instance_id: OTel service.instance.id — pod name / hostname.
    log_level: str = "INFO"
    log_format: str = "json"
    log_otlp_push: bool = True
    service_namespace: str | None = None
    service_instance_id: str | None = None
    worker_build_id: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
