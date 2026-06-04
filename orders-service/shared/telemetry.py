"""
Observability initialisation — call init_observability() once per process.

Two-pipeline model
------------------
PULL  (Prometheus scrape)   — Temporal SDK + custom metrics via metric_meter().
                              PrometheusConfig binds /metrics on sdk_metrics_port.
                              Scraped by Prometheus inside the lgtm container.

PUSH  (OTLP gRPC to lgtm)  — Traces → Tempo, Logs → Loki,
                              Business metrics → Prometheus (via OTel Collector).
                              Use business_meter() in activities and the API.
                              Workflow code MUST use workflow.metric_meter() instead
                              (replay-safe; rides the PULL pipeline).
"""
from __future__ import annotations

import logging

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig


class Telemetry:
    """Holds the initialised telemetry providers and Temporal Runtime."""

    def __init__(
        self,
        runtime: Runtime,
        interceptors: list,
        tracer_provider: TracerProvider,
        meter_provider: MeterProvider,
        logger_provider: LoggerProvider,
    ) -> None:
        self.runtime = runtime
        self.interceptors = interceptors
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._logger_provider = logger_provider

    def shutdown(self) -> None:
        """Flush all in-flight telemetry before process exit."""
        self._tracer_provider.force_flush()
        self._tracer_provider.shutdown()
        self._meter_provider.force_flush(timeout_millis=5_000)
        self._meter_provider.shutdown()
        self._logger_provider.force_flush()
        self._logger_provider.shutdown()


def init_observability(
    service_name: str,
    otlp_endpoint: str = "http://localhost:4317",
    sdk_metrics_port: int = 9000,
) -> Telemetry:
    """
    Initialise the full observability stack for one process.

    Parameters
    ----------
    service_name:     Shown in Grafana as the service label (set per-process).
    otlp_endpoint:    OTLP gRPC endpoint for traces, logs, and business metrics.
    sdk_metrics_port: Port for the Temporal SDK Prometheus pull endpoint.
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

    # ── Logs (OTLP push → Loki) ─────────────────────────────────────────────
    # Attaches a LoggingHandler to the root logger so all existing
    # workflow.logger / activity.logger output is forwarded to Loki.
    # Root logger default level is WARNING; set to INFO so activity/workflow
    # INFO logs are not filtered before reaching the OTel handler.
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=otlp_endpoint))
    )
    set_logger_provider(logger_provider)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(
        LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
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
        logger_provider=logger_provider,
    )
