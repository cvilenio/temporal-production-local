"""
Dependency container — single dependency shop for all processes.

No dependency-injector "wiring" is used (no @inject / Provide[] / container.wire()).
Providers are resolved explicitly via the thin accessor functions in resources.py
and FastAPI's own Depends().

Why dependency-injector over @lru_cache: the `telemetry` Resource provider gives
deterministic init/teardown. container.init_resources() starts the OTel providers
and the Prometheus metrics endpoint; container.shutdown_resources() flushes and
shuts them down before the process exits — handled by the container, not hand-rolled.

Entrypoint pattern (sync resources → do NOT await):
    from orders_kernel.resources import container
    container.config.otel_service_name.override("orders-worker-activity")
    container.init_resources()        # starts telemetry
    ...
    container.shutdown_resources()    # flushes telemetry (in finally / teardown)
"""

from collections.abc import Iterator

from dependency_injector import containers, providers

from orders_kernel.clients.mock_api import MockApiClient
from orders_kernel.clients.orders_service import OrdersServiceClient
from orders_kernel.db.engine import Database
from orders_kernel.services.temporal import TemporalService
from orders_kernel.shared.telemetry import Telemetry, init_observability


def _telemetry_resource(
    service_name: str,
    otlp_endpoint: str,
    sdk_metrics_port: int,
) -> Iterator[Telemetry]:
    """Sync generator resource: init → yield → shutdown on container teardown.

    Kept synchronous on purpose: an async generator resource resolves to a
    coroutine when accessed (including via .provided), which breaks the
    synchronous accessors and the temporal_service Singleton below. With a sync
    resource, init_resources()/shutdown_resources() run inline and .provided
    resolves to the real attribute value.
    """
    tel = init_observability(service_name, otlp_endpoint, int(sdk_metrics_port))
    yield tel
    tel.shutdown()


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()

    # ── Telemetry (Resource — owns init + shutdown lifecycle) ────────────────
    telemetry: providers.Resource[Telemetry] = providers.Resource(
        _telemetry_resource,
        service_name=config.otel_service_name,
        otlp_endpoint=config.otel_exporter_otlp_endpoint,
        sdk_metrics_port=config.sdk_metrics_port,
    )

    # ── Singletons ────────────────────────────────────────────────────────────
    database = providers.Singleton(
        Database,
        db_url=config.database_url,
    )

    mock_api = providers.Singleton(
        MockApiClient,
        base_url=config.mock_api_url,
    )

    orders_service_client = providers.Singleton(
        OrdersServiceClient,
        base_url=config.orders_service_url,
    )

    # TemporalService takes the per-process OTel Runtime + TracingInterceptor from
    # the telemetry resource. `telemetry.provided.runtime` resolves to the real
    # attribute once the (sync) resource is initialised — no Futures involved.
    temporal_service = providers.Singleton(
        TemporalService,
        temporal_address=config.temporal_address,
        temporal_namespace=config.temporal_namespace,
        runtime=telemetry.provided.runtime,
        interceptors=telemetry.provided.interceptors,
        tls=config.temporal_tls,
        api_key=config.temporal_api_key,
        tls_client_cert_path=config.temporal_tls_client_cert_path,
        tls_client_key_path=config.temporal_tls_client_key_path,
    )
