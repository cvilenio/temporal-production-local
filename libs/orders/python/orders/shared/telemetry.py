"""
Observability initialisation — call init_observability() once per process.

Three signals, two transports
-----------------------------
PULL  (Prometheus scrape)   — Temporal SDK + custom metrics via metric_meter().
                              PrometheusConfig binds /metrics on sdk_metrics_port.
                              Scraped by Prometheus inside the lgtm container.

PUSH  (OTLP gRPC to lgtm)  — Traces → Tempo, Business metrics → Prometheus
                              (via OTel Collector). Use business_meter() in
                              activities and the API. Workflow code MUST use
                              workflow.metric_meter() instead (replay-safe; rides
                              the PULL pipeline).

LOGS                        — Owned by `obslog` (the shared logging kernel), not
                              wired here. Logs always render JSON to stdout (for
                              the Alloy DaemonSet to tail on Kubernetes / Docker
                              Desktop on the host) and, when log_otlp_push is on
                              (host plane, no node agent), ALSO push OTLP → Loki.
                              See ADR-0018. init_observability just forwards the
                              log settings into obslog.init_logging.
"""

from __future__ import annotations

import obslog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig


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
    otlp_endpoint:    OTLP gRPC endpoint for traces, business metrics, and
                      (when log_otlp_push) logs.
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

    # ── Business metrics (OTLP push → Prometheus via OTel Collector) ────────
    # Use business_meter() in activities and the API.
    # Workflow code must use workflow.metric_meter() (replay-safe).
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=otlp_endpoint),
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

    # ── Logs (obslog: stdout JSON always; OTLP → Loki when log_otlp_push) ────
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
    # durations_as_seconds=True → latency histogram VALUES are in seconds (not the
    # SDK default of milliseconds). It does NOT add a `_seconds` name suffix — that
    # is a separate exporter option (unit_suffix, default off, left unset here), so
    # histograms are exposed as bare `temporal_*_latency_bucket` with second-scale
    # values. (counters_total_suffix is likewise unset → counters carry no `_total`.)
    # The bundled sdk.json + critical-flows dashboards query these bare names.
    #
    # NOTE: the Python SDK's Prometheus exporter exposes counters WITHOUT a
    # `_total` suffix (e.g. `temporal_workflow_completed`, not
    # `..._completed_total`). counters_total_suffix is a no-op in this SDK build,
    # so we don't set it — and the bundled sdk.json has been aligned to the
    # no-`_total` names. (Counters pushed via OTLP, e.g. business metrics, DO get
    # `_total` added by the collector's Prometheus exporter — a separate path.)
    runtime = Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(
                bind_address=f"0.0.0.0:{sdk_metrics_port}",
                durations_as_seconds=True,
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
