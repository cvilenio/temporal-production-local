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
    # Cluster-visibility UIs, fronted by viz-proxy (frame headers stripped).
    # Live only while the kind cluster is up; see ADR-0014.
    headlamp_embed_url: str = "http://localhost:8087"
    argocd_embed_url: str = "http://localhost:8088"

    # Cloud mode: the Temporal UI is the hosted Cloud console, which can't be
    # iframed (X-Frame-Options: SAMEORIGIN) and uses OAuth — so the nav links
    # out to it in a new tab instead. Several namespaces are live at once, so we
    # open the namespaces list rather than deep-linking one (which also avoids
    # depending on a single, possibly-stale TEMPORAL_NAMESPACE). The presence of
    # temporal_namespace is just the "pointed at Cloud" signal; its value isn't
    # used in the URL. temporal_ui_external_url overrides the target if set.
    temporal_namespace: str = ""
    temporal_ui_external_url: str = ""

    @property
    def temporal_cloud_url(self) -> str:
        if self.temporal_ui_external_url:
            return self.temporal_ui_external_url
        if self.temporal_namespace:
            return "https://cloud.temporal.io/namespaces"
        return ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


# database_url has no default; pydantic-settings populates it from the
# environment at runtime, which the type checker can't see.
settings = Settings()  # pyright: ignore[reportCallIssue]
