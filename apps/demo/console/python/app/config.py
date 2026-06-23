from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    orders_service_url: str = "http://orders-service:8000"
    port: int = 8086
    log_buffer_size: int = 500
    order_poll_interval_seconds: int = 3
    orders_service_timeout_seconds: int = 60

    # Browser-reachable URLs for embedded tool UIs (iframed in the console).
    # These resolve in the user's browser, not inside the container, so they
    # point at host-published ports. Temporal UI is served via the nginx
    # ui-proxy (8081), which strips X-Frame-Options so it can be framed.
    temporal_ui_embed_url: str = "http://localhost:8081"
    grafana_embed_url: str = "http://localhost:3000"
    pgweb_embed_url: str = "http://localhost:8083"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


# database_url has no default; pydantic-settings populates it from the
# environment at runtime, which the type checker can't see.
settings = Settings()  # pyright: ignore[reportCallIssue]
