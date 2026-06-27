"""Orders REST API settings (ADR-0022 composition root).

Composes the generic appkit field-groups it needs — the Temporal connection profile
and telemetry — with the API's own deltas. The API never calls mock-api or the orders
service directly, so it carries neither of those URLs; it doesn't run a worker, so it
carries no worker-tuning knobs.
"""

from appkit import TelemetrySettings, TemporalConnectionSettings
from pydantic_settings import SettingsConfigDict


class Settings(TemporalConnectionSettings, TelemetrySettings):
    # Domain defaults overriding the neutral appkit mixin defaults.
    temporal_namespace: str = "ziggymart"
    otel_service_name: str = "orders-service"

    database_url: str = "postgresql+asyncpg://admin:password@localhost:5433/orders_db"

    # Demo-only kill switch for the destructive /admin/reset endpoint (terminate
    # workflows + truncate app tables). Defaults on for the local demo; set
    # DEMO_RESET_ENABLED=false to disable in any shared environment.
    demo_reset_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
