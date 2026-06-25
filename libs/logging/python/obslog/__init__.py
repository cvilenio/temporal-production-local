"""
obslog — the polyglot structured-logging kernel (Python reference impl).

One facade, one schema, two sinks. Every service calls :func:`init_logging`
once at startup; thereafter all logs — whether emitted via this package's
:func:`get_logger`, via stdlib ``logging``, or via Temporal's replay-safe
``workflow.logger`` / ``activity.logger`` — render through a single structlog
processor pipeline into the **same JSON schema** and land on:

  1. **stdout** (always) — JSON lines for ``kubectl logs`` / Headlamp / Docker
     Desktop, and for a node agent (Grafana Alloy) to tail in Kubernetes.
  2. **OTLP → Loki** (optional, ``otlp_endpoint`` set) — for host-plane
     services that ship directly to the observability backend (no node agent).

The schema (see ADR-0018):
  resource : service.name, service.namespace, service.instance.id, service.version
  core     : timestamp (ISO-8601 UTC), level, logger, event
  context  : bound key/values — Temporal (workflow_id, run_id, …) + business
             (order_id, trace_id, step). Concurrency-safe via contextvars.

Why structlog: its processor pipeline is the same shape as Temporal's
DataConverter/PayloadCodec — ordered encoding steps ending in a wire format —
so the type-robust :mod:`obslog.serialize` codec slots in as one processor and
the OTel logs data model is the language-neutral wire schema other SDKs mirror.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any

import structlog

from obslog import schema
from obslog.serialize import json_fallback, safe_serialize

__all__ = ["init_logging", "get_logger", "bound", "wf_log_extra", "LoggingHandle"]

# Process-wide resource identity (service.name, service.namespace, …). Injected
# into every record by a processor rather than via contextvars: request/activity
# handlers run in tasks spawned from a different context than init_logging, so a
# contextvar set at startup would not reliably propagate to them. A module global
# always does.
_RESOURCE: dict[str, Any] = {}


def _inject_resource(_logger: Any, _method: str, event_dict: dict) -> dict:
    for key, value in _RESOURCE.items():
        event_dict.setdefault(key, value)
    return event_dict


class LoggingHandle:
    """Owns process-lifetime logging resources. Hold it and call
    :meth:`shutdown` on exit to flush any OTLP log batch before the process
    dies (otherwise the last records are lost)."""

    def __init__(self, otlp_provider: Any | None) -> None:
        self._otlp_provider = otlp_provider

    def shutdown(self) -> None:
        if self._otlp_provider is not None:
            self._otlp_provider.force_flush()
            self._otlp_provider.shutdown()


def _timestamper() -> Any:
    return structlog.processors.TimeStamper(fmt="iso", utc=True)


def _common_processors() -> list:
    """Processors shared by structlog-native and foreign (stdlib) records, so
    every record — including ``workflow.logger`` / ``activity.logger`` — carries
    the same core fields and bound context."""
    return [
        structlog.contextvars.merge_contextvars,
        _inject_resource,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _timestamper(),
    ]


def _build_formatter(fmt: str) -> structlog.stdlib.ProcessorFormatter:
    if fmt == "console":
        render = [safe_serialize, structlog.dev.ConsoleRenderer()]
    else:
        render = [
            # Structured, JSON-safe traceback for any exc_info=True call.
            # show_locals=False: frame locals are noisy and can leak PII/secrets.
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            safe_serialize,
            structlog.processors.JSONRenderer(default=json_fallback),
        ]
    return structlog.stdlib.ProcessorFormatter(
        # ExtraAdder pulls stdlib ``extra={...}`` (the workflow/activity logger
        # context, plus the SDK's injected workflow/activity info) into the dict.
        foreign_pre_chain=[*_common_processors(), structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *render,
        ],
    )


def _build_otlp_provider(
    endpoint: str, resource_attrs: dict[str, Any]
) -> tuple[Any, logging.Handler]:
    """Lazily build an OTel LoggerProvider + LoggingHandler. Imported here so
    obslog has no hard OpenTelemetry dependency — only services that push OTLP
    (e.g. host-plane mock-api, compose workers) pull it in."""
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    provider = LoggerProvider(resource=Resource.create(resource_attrs))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint))
    )
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    return provider, handler


def init_logging(
    service_name: str,
    *,
    level: str = "INFO",
    fmt: str = "json",
    otlp_endpoint: str | None = None,
    namespace: str | None = None,
    instance_id: str | None = None,
    version: str | None = None,
) -> LoggingHandle:
    """Configure the process logging pipeline once. Idempotent-ish: re-running
    resets the root handlers, so the last call wins.

    Parameters
    ----------
    service_name : OTel ``service.name`` — the Grafana/Loki service label.
    level        : Root log level (e.g. "INFO", "DEBUG").
    fmt          : "json" (shipped) or "console" (pretty local dev).
    otlp_endpoint: If set, ALSO push logs via OTLP to this gRPC endpoint
                   (host-plane services with no node agent). On Kubernetes leave
                   this unset — the Alloy DaemonSet tails stdout instead.
    namespace    : OTel ``service.namespace`` (the domain, e.g. "ziggymart").
    instance_id  : OTel ``service.instance.id`` (pod name / hostname).
    version      : OTel ``service.version`` (worker Build ID when present).
    """
    structlog.configure(
        processors=[
            *_common_processors(),
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            # Hand off to the stdlib formatter so structlog and foreign records
            # share one renderer (one schema).
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = _build_formatter(fmt)

    root = logging.getLogger()
    # Replace any handlers from a prior init or a library's basicConfig so we
    # have a single, predictable pipeline.
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    # Resource identity, injected into every record by _inject_resource (stdout
    # path) and carried as the OTLP LoggerProvider's Resource (push path). Keys
    # come from the shared schema (obslog.schema) — single source of truth.
    resource_attrs: dict[str, Any] = {schema.SERVICE_NAME: service_name}
    if namespace:
        resource_attrs[schema.SERVICE_NAMESPACE] = namespace
    if instance_id:
        resource_attrs[schema.SERVICE_INSTANCE_ID] = instance_id
    if version:
        resource_attrs[schema.SERVICE_VERSION] = version
    _RESOURCE.clear()
    _RESOURCE.update(resource_attrs)

    otlp_provider = None
    if otlp_endpoint:
        otlp_provider, otlp_handler = _build_otlp_provider(
            otlp_endpoint, resource_attrs
        )
        # The OTel handler ships the record's own fields/extra; it does not run
        # the structlog formatter (Loki/collector parse the OTLP body + attrs).
        root.addHandler(otlp_handler)

    root.setLevel(level.upper())

    return LoggingHandle(otlp_provider)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Use in plain async code (API, mock-api,
    console, db). In workflows use ``workflow.logger`` + :func:`wf_log_extra`;
    in activities use ``activity.logger`` + :func:`bound`."""
    return structlog.stdlib.get_logger(name)


@contextmanager
def bound(**ctx: Any):
    """Context manager that enriches every log emitted within it with ``ctx``.

    Concurrency-safe: backed by ``structlog.contextvars`` (per-coroutine /
    per-thread), so two orders processed concurrently never see each other's
    context. Safe in activities and plain async code — NOT inside a workflow
    definition (use :func:`wf_log_extra` there; see ADR-0018 replay boundary).
    """
    tokens = structlog.contextvars.bind_contextvars(**ctx)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


def wf_log_extra(**ctx: Any) -> dict[str, Any]:
    """Build an ``extra`` dict for ``workflow.logger`` calls inside a workflow
    definition.

    Workflows must NOT use contextvars (:func:`bound`): contextvar state across
    the deterministic sandbox / replay is a footgun. Instead, bind context
    explicitly from deterministic workflow state and pass it here — the SDK's
    ``workflow.logger`` already injects workflow id/run id/type and suppresses
    duplicate lines on replay. See ADR-0018.
    """
    return {k: v for k, v in ctx.items() if v is not None}
