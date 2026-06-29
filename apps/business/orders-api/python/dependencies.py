"""Dependency providers for the Orders REST API — the composition root (ADR-0022).

This app wires only the ports it actually uses: telemetry (a Resource — it owns the
init/shutdown lifecycle) and the database (a Singleton). The Temporal client is async to
construct, so it is built in the app lifespan (see main.py) from `appkit.build_temporal_client`
— which bakes in the data-converter contract — and the domain `TemporalService` is wrapped
around it there. Provider *lifetimes* are this app's choice (policy); the converter is not
(contract).

No dependency-injector "wiring" (@inject / Provide[]) is used; providers are resolved
explicitly via the accessors below and FastAPI's Depends().
"""

from appkit import Database, Telemetry, telemetry_resource
from dependency_injector import containers, providers
from fastapi import Depends, Request
from orders.services.temporal import TemporalService
from settings import settings


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()

    # Telemetry (Resource — owns init + shutdown). Sync resource so .provided and the
    # synchronous accessors resolve to real values (see appkit.telemetry_resource).
    telemetry: providers.Resource[Telemetry] = providers.Resource(
        telemetry_resource,
        service_name=config.otel_service_name,
        otlp_endpoint=config.otel_exporter_otlp_endpoint,
        metrics_otlp_endpoint=config.otel_exporter_otlp_metrics_endpoint,
        sdk_metrics_port=config.sdk_metrics_port,
        log_level=config.log_level,
        log_format=config.log_format,
        log_otlp_push=config.log_otlp_push,
        namespace=config.service_namespace,
        instance_id=config.service_instance_id,
        version=config.worker_build_id,
    )

    database = providers.Singleton(Database, db_url=config.database_url)


container = Container()
container.config.from_pydantic(settings)


# ── FastAPI dependency accessors (the DI → request bridge) ────────────────────
def get_database() -> Database:
    return container.database()


def get_temporal_service(request: Request) -> TemporalService:
    # Connected once in the lifespan and stashed on app.state — domain service over
    # the shared-converter client (see main.py).
    return request.app.state.temporal_service


async def get_db_session(db: Database = Depends(get_database)):
    async for session in db.get_session():
        yield session
