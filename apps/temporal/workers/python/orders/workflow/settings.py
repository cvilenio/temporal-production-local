"""Orders workflow worker settings (ADR-0022 composition root).

Composes the generic appkit field-groups it needs — Temporal connection profile, worker
tuning, telemetry — with its deltas. The workflow worker hosts no activities, so it carries
no mock-api / orders-service URLs.
"""

from appkit import TelemetrySettings, TemporalConnectionSettings, WorkerTuningSettings
from pydantic_settings import SettingsConfigDict


class Settings(TemporalConnectionSettings, WorkerTuningSettings, TelemetrySettings):
    temporal_namespace: str = ""
    otel_service_name: str = "orders-worker-workflow"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
