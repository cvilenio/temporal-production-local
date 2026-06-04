from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "ziggymart"
    database_url: str = "postgresql+asyncpg://admin:password@localhost:5433/orders_db"
    mock_api_url: str = "http://localhost:8001"
    orders_service_url: str = "http://localhost:8002"

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

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
