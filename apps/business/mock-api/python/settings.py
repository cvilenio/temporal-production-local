"""Mock External Systems API settings (ADR-0022 composition root).

Standalone host-plane mock — it does NOT use appkit (no Temporal, no DB), so it keeps a
small self-contained Settings: structured-logging config plus the simulated latency /
failure knobs the scenarios drive.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Structured logging (obslog). Host-plane mock pushes OTLP straight to the backend
    # (no node agent collects host containers); on Kubernetes this would flip to
    # stdout-only. See ADR-0018.
    otel_service_name: str = "mock-api"
    otel_exporter_otlp_endpoint: str = "http://lgtm:4317"
    log_level: str = "INFO"
    log_format: str = "json"
    log_otlp_push: bool = True
    service_namespace: str | None = None

    # Simulated response latencies / hangs (ms) the demo scenarios drive.
    mock_payment_latency_ms: int = 5000
    mock_inventory_latency_ms: int = 5000
    mock_shipping_latency_ms: int = 8000
    mock_shipping_hang_ms: int = 15000

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
