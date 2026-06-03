from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    orders_service_url: str = "http://orders-service:8000"
    port: int = 8086
    log_buffer_size: int = 500
    order_poll_interval_seconds: int = 3
    orders_service_timeout_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
