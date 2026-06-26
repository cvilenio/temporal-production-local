"""Conformance: obslog's emitted records satisfy the shared log schema (ADR-0018).

This is the drift guard between the Python emitter and the language-neutral
contract (libs/logging/schema/log-schema.json). A future Go/TS emitter ships its
own equivalent test against the SAME contract.
"""

from __future__ import annotations

import json

from obslog import bound, get_logger, init_logging, schema


def _last_record(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_record_has_required_fields_and_bound_context(capsys):
    init_logging("svc-test", fmt="json", namespace="ziggymart", instance_id="i-1")
    log = get_logger("t")
    with bound(order_id="O-1", trace_id="T-1"):
        log.info("hello", qty=2)

    rec = _last_record(capsys)

    # Contract: required fields present.
    assert schema.missing_required(rec) == []
    # Resource identity injected (not via contextvars — survives task hops).
    assert rec[schema.SERVICE_NAME] == "svc-test"
    assert rec[schema.SERVICE_NAMESPACE] == "ziggymart"
    # Core + bound business context + per-call field all present.
    assert rec[schema.LEVEL] == "info"
    assert rec[schema.MESSAGE] == "hello"
    assert rec["order_id"] == "O-1"
    assert rec["trace_id"] == "T-1"
    assert rec["qty"] == 2


def test_foreign_stdlib_extra_conforms(capsys):
    """workflow.logger / activity.logger emit via stdlib with extra={...}; those
    records must land in the same schema (ExtraAdder + shared processors)."""
    import logging

    init_logging("svc-test", fmt="json")
    logging.getLogger("orders.workflow").info(
        "step completed", extra={"step": "capture_payment", "order_id": "O-9"}
    )

    rec = _last_record(capsys)
    assert schema.missing_required(rec) == []
    assert rec["step"] == "capture_payment"
    assert rec["order_id"] == "O-9"


def test_schema_file_is_loadable_and_well_formed():
    s = schema.load_schema()
    assert s["required"]
    for field in schema.REQUIRED_FIELDS:
        assert field in s["properties"], f"{field} missing from schema properties"
