"""{{Domain}} activity worker settings."""

from appkit import TelemetrySettings, TemporalConnectionSettings, WorkerTuningSettings
from pydantic_settings import SettingsConfigDict


class Settings(TemporalConnectionSettings, WorkerTuningSettings, TelemetrySettings):
    temporal_namespace: str = ""
    otel_service_name: str = "{{DOMAIN}}-worker-activity"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
