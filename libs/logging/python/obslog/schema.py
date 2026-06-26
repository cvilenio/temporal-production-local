"""
The shared schema — canonical field names and the contract location.

The authoritative, language-neutral contract is ``libs/logging/schema/
log-schema.json`` (ADR-0018). This module mirrors its well-known keys as Python
constants so the Python emitter and its conformance test reference one source of
truth instead of scattering magic strings. A future ``libs/logging/go`` /
``libs/logging/ts`` mirrors the SAME json — the schema is shared, the emitters
are per-language.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Resource (OTel resource attributes) ─────────────────────────────────────
SERVICE_NAME = "service.name"
SERVICE_NAMESPACE = "service.namespace"
SERVICE_INSTANCE_ID = "service.instance.id"
SERVICE_VERSION = "service.version"
RESOURCE_FIELDS = (
    SERVICE_NAME,
    SERVICE_NAMESPACE,
    SERVICE_INSTANCE_ID,
    SERVICE_VERSION,
)

# ── Record core ─────────────────────────────────────────────────────────────
TIMESTAMP = "timestamp"
LEVEL = "level"
LOGGER = "logger"
# The human-readable message. Named `message` (not structlog's default `event`)
# to opt into the cross-tool convention — OTel maps it to the LogRecord Body,
# and agents (Cloud Logging, Datadog, our Alloy filelog receiver) recognize
# `message` as the display line by convention. ADR-0018 / checkpoint 0017.
MESSAGE = "message"
CORE_FIELDS = (TIMESTAMP, LEVEL, LOGGER, MESSAGE)

# ── Context (bound) ─────────────────────────────────────────────────────────
TEMPORAL_CONTEXT_FIELDS = (
    "workflow_id",
    "run_id",
    "workflow_type",
    "activity_id",
    "activity_type",
    "attempt",
    "task_queue",
)
BUSINESS_CONTEXT_FIELDS = ("order_id", "trace_id", "step", "request_id")

# Minimum every conformant record must carry.
REQUIRED_FIELDS = (TIMESTAMP, LEVEL, LOGGER, MESSAGE, SERVICE_NAME)

# Location of the shared, language-neutral contract (sibling to python/).
SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schema" / "log-schema.json"


def load_schema() -> dict:
    """Load the JSON Schema contract. Other tooling (or CI with jsonschema) can
    validate against it; the bundled conformance test uses the lightweight
    :func:`missing_required` instead to avoid a hard jsonschema dependency."""
    return json.loads(SCHEMA_FILE.read_text())


def missing_required(record: dict) -> list[str]:
    """Return the required schema fields absent from ``record`` (empty == OK)."""
    return [field for field in REQUIRED_FIELDS if field not in record]
