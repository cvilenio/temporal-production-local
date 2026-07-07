"""
Observability initialisation — call init_observability() once per process.

Three signals, two transports
-----------------------------
PULL  (Prometheus scrape)   — Temporal SDK + custom metrics via metric_meter().
                              PrometheusConfig binds /metrics on sdk_metrics_port.
                              Scraped by Prometheus inside the lgtm container.

PUSH  (OTLP gRPC)          — Traces → Tempo (lgtm). Business metrics → ClickHouse
                              via the standalone OTel Collector (ADR-0024), on a
                              SEPARATE endpoint (metrics_otlp_endpoint) so they do
                              not land in Tempo's lgtm collector. Use business_meter()
                              in activities and the API. Workflow code MUST use
                              workflow.metric_meter() instead (replay-safe; rides
                              the PULL pipeline). Push metrics use DELTA temporality
                              — the warehouse-natural shape for ClickHouse.

LOGS                        — Owned by `obslog` (the shared logging kernel), not
                              wired here. Logs always render JSON to stdout (for
                              the Alloy DaemonSet to tail on Kubernetes / Docker
                              Desktop on the host) and, when log_otlp_push is on
                              (host plane, no node agent), ALSO push OTLP → the
                              OTel Collector → ClickHouse (ADR-0018/0020).
                              init_observability just forwards the log settings
                              into obslog.init_logging.
"""

from __future__ import annotations

from collections.abc import Iterator

import obslog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import (
    Counter,
    Histogram,
    MeterProvider,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

# Delta temporality preference for the OTLP push pipeline (ADR-0024). Delta is the
# warehouse-natural shape: each export carries the increment for its window, so a
# SQL sum(Value) over a time range is an exact count in ClickHouse — no Prometheus-
# style rate() over cumulative series needed. UpDownCounter/Gauge stay cumulative
# (a delta on a level has no meaning).
_DELTA_TEMPORALITY = {
    Counter: AggregationTemporality.DELTA,
    Histogram: AggregationTemporality.DELTA,
    ObservableCounter: AggregationTemporality.DELTA,
    UpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableGauge: AggregationTemporality.CUMULATIVE,
}


class Telemetry:
    """Holds the initialised telemetry providers, the logging handle, and the
    Temporal Runtime."""

    def __init__(
        self,
        runtime: Runtime,
        interceptors: list,
        tracer_provider: TracerProvider,
        meter_provider: MeterProvider,
        log_handle: obslog.LoggingHandle,
    ) -> None:
        self.runtime = runtime
        self.interceptors = interceptors
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._log_handle = log_handle

    def shutdown(self) -> None:
        """Flush all in-flight telemetry before process exit."""
        self._tracer_provider.force_flush()
        self._tracer_provider.shutdown()
        self._meter_provider.force_flush(timeout_millis=5_000)
        self._meter_provider.shutdown()
        self._log_handle.shutdown()


def init_observability(
    service_name: str,
    otlp_endpoint: str = "http://localhost:4317",
    sdk_metrics_port: int = 9000,
    *,
    metrics_otlp_endpoint: str | None = None,
    log_level: str = "INFO",
    log_format: str = "json",
    log_otlp_push: bool = True,
    namespace: str | None = None,
    instance_id: str | None = None,
    version: str | None = None,
) -> Telemetry:
    """
    Initialise the full observability stack for one process.

    Parameters
    ----------
    service_name:     Shown in Grafana as the service label (set per-process).
    otlp_endpoint:    OTLP gRPC endpoint for traces and (when log_otlp_push) logs.
    metrics_otlp_endpoint: OTLP gRPC endpoint for business (push) metrics. Defaults
                      to otlp_endpoint when None. Point at the standalone OTel
                      Collector so business metrics land in ClickHouse (ADR-0024).
    sdk_metrics_port: Port for the Temporal SDK Prometheus pull endpoint.
    log_level/format: Forwarded to obslog (root level; "json"|"console").
    log_otlp_push:    Push logs over OTLP too (host plane). False on Kubernetes,
                      where the Alloy DaemonSet collects stdout. See ADR-0018.
    namespace:        OTel service.namespace (the domain, e.g. "ziggymart").
    instance_id:      OTel service.instance.id (pod name / hostname).
    version:          OTel service.version (worker Build ID when present).
    """
    resource = Resource.create({SERVICE_NAME: service_name})

    # ── Traces (OTLP push → Tempo) ──────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
    )
    trace.set_tracer_provider(tracer_provider)

    # ── Business metrics (OTLP push → ClickHouse via standalone OTel Collector) ─
    # Use business_meter() in activities and the API.
    # Workflow code must use workflow.metric_meter() (replay-safe).
    # Pushed on metrics_otlp_endpoint (the standalone collector), distinct from the
    # trace endpoint (lgtm/Tempo), with DELTA temporality for ClickHouse (ADR-0024).
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=metrics_otlp_endpoint or otlp_endpoint,
                    preferred_temporality=_DELTA_TEMPORALITY,
                ),
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Temporal logger hygiene (ADR-0018) ──────────────────────────────────
    # By default the SDK appends a string repr of the workflow/activity info to
    # every message, so `event` reads "order workflow started ({'attempt': 1,
    # ...})". We keep the message CLEAN and rely on the structured extras
    # (`temporal_workflow` / `temporal_activity`) the SDK still attaches — so the
    # JSON `event` is just the message, with context in its own fields (the
    # message-first / expandable-context experience in Loki/Grafana).
    from temporalio import activity as _activity
    from temporalio import workflow as _workflow

    _workflow.logger.workflow_info_on_message = False
    _workflow.logger.workflow_info_on_extra = True
    _activity.logger.activity_info_on_message = False
    _activity.logger.activity_info_on_extra = True

    # ── Logs (obslog: stdout JSON always; OTLP → collector→ClickHouse when push) ─
    # obslog owns the root-logger pipeline so that workflow.logger /
    # activity.logger and every stdlib logger render through one schema.
    log_handle = obslog.init_logging(
        service_name,
        level=log_level,
        fmt=log_format,
        otlp_endpoint=otlp_endpoint if log_otlp_push else None,
        namespace=namespace,
        instance_id=instance_id,
        version=version,
    )

    # ── Temporal SDK operational metrics (Prometheus pull) ───────────────────
    # PrometheusConfig binds /metrics on 0.0.0.0:<port>, scraped by Prometheus
    # inside the lgtm container.
    #
    # OpenMetrics naming is the repo's cross-SDK metric contract (ADR-0027):
    # counters carry `_total`, duration histograms carry `_seconds`, values in seconds.
    # Java (Micrometer default) and Go (`NewPrometheusNamingScope`) emit the same
    # suffixed names natively; Python opts in via these PrometheusConfig toggles.
    # Gauges are unchanged. Business metrics pushed via OTLP get `_total` from the
    # collector's Prometheus exporter — a separate path (ADR-0024).
    runtime = Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(
                bind_address=f"0.0.0.0:{sdk_metrics_port}",
                durations_as_seconds=True,
                counters_total_suffix=True,
                unit_suffix=True,
            )
        )
    )

    return Telemetry(
        runtime=runtime,
        interceptors=[TracingInterceptor()],
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        log_handle=log_handle,
    )


def telemetry_resource(
    service_name: str,
    otlp_endpoint: str,
    sdk_metrics_port: int,
    log_level: str,
    log_format: str,
    log_otlp_push: bool,
    namespace: str | None,
    instance_id: str | None,
    version: str | None,
    metrics_otlp_endpoint: str | None = None,
) -> Iterator[Telemetry]:
    """Sync generator resource: init → yield → shutdown on teardown.

    Shaped for a DI provider (e.g. dependency-injector `providers.Resource`) but
    imports no DI framework — it's just a generator an app's composition root wraps.
    Kept synchronous on purpose: an async generator resource resolves to a coroutine
    when accessed (including via `.provided`), which breaks synchronous accessors and
    any Singleton built from it. With a sync resource, init/shutdown run inline and
    `.provided` resolves to the real attribute value.
    """
    tel = init_observability(
        service_name,
        otlp_endpoint,
        int(sdk_metrics_port),
        metrics_otlp_endpoint=metrics_otlp_endpoint,
        log_level=log_level,
        log_format=log_format,
        log_otlp_push=bool(log_otlp_push),
        namespace=namespace,
        instance_id=instance_id,
        version=version,
    )
    yield tel
    tel.shutdown()
