from appkit import (
    TelemetrySettings,
    TemporalConnectionSettings,
    WorkerTuningSettings,
)
from pydantic_settings import SettingsConfigDict


class Settings(TemporalConnectionSettings, WorkerTuningSettings, TelemetrySettings):
    """Orders settings: the generic appkit field-groups (connection profile, worker
    tuning, telemetry) composed with the orders-specific deltas below.

    NOTE (ADR-0022): this lives in the lib only transiently. Composition belongs to
    the deployable — PR B moves these settings into each app's composition root.
    """

    # Domain-specific defaults overriding the generic appkit mixins.
    temporal_namespace: str = "ziggymart"
    otel_service_name: str = "orders-service"

    # ── Orders deltas (3b — domain composition) ───────────────────────────────
    database_url: str = "postgresql+asyncpg://admin:password@localhost:5433/orders_db"
    mock_api_url: str = "http://localhost:8001"
    orders_service_url: str = "http://localhost:8002"

    # Demo-only kill switch for the destructive /admin/reset endpoint
    # (terminate workflows + truncate app tables). Defaults on for the local
    # demo; set DEMO_RESET_ENABLED=false to disable in any shared environment.
    demo_reset_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
