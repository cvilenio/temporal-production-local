"""Orders activity worker settings (ADR-0022 composition root).

Composes the generic appkit field-groups it needs — Temporal connection profile, worker
tuning, telemetry — with its deltas: the mock-api and orders-service URLs its activities call.
"""

from appkit import TelemetrySettings, TemporalConnectionSettings, WorkerTuningSettings
from pydantic_settings import SettingsConfigDict


class Settings(TemporalConnectionSettings, WorkerTuningSettings, TelemetrySettings):
    temporal_namespace: str = ""
    otel_service_name: str = "orders-worker-activity"

    # Ports this worker's activities call.
    mock_api_url: str = "http://localhost:8001"
    orders_service_url: str = "http://localhost:8002"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
