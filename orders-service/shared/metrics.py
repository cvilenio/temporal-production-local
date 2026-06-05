"""
Business metrics helpers.

Use business_meter() in activities and the API service to record application-
level metrics.  These are pushed via OTLP to Prometheus (through the OTel
Collector in lgtm).

Workflow code MUST use workflow.metric_meter() instead — it is replay-safe and
automatically suppresses emission during Temporal history replay.  Using a push-
based meter inside a workflow would double-count on replay and is not deterministic.

Example (activity):
    from shared.metrics import business_meter

    meter = business_meter()
    payment_counter = meter.create_counter(
        "orders.payments_captured",
        description="Number of payments captured",
    )
    payment_counter.add(1, {"currency": "usd"})

Example (workflow — use the SDK meter instead):
    from temporalio import workflow

    counter = workflow.metric_meter().create_counter(
        "orders.workflow_steps_completed",
        description="Steps completed within the order workflow",
    )
    counter.add(1, {"step": "reserve_inventory"})
"""

from opentelemetry import metrics


def business_meter() -> metrics.Meter:
    """Return the shared OTel Meter for business metrics (OTLP push pipeline)."""
    return metrics.get_meter("ziggymart.business")
