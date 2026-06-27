from temporalio import workflow

from orders.shared.workflow_io import OrderWorkflowInput


class OrderRunContext:
    """Order-specific runtime context for the workflow."""

    def __init__(self, input: OrderWorkflowInput, workflow_id: str):
        self.order_id = input.order_id
        self.workflow_id = workflow_id
        self.item_id = input.item_id
        self.quantity = input.quantity
        self.user_id = input.user_id
        self.address = input.address
        self.payment_authorization_id = input.payment_authorization_id
        # Money in minor units (cents) — see ADR-0021.
        self.amount_minor = input.amount_minor
        # proto3 strings default to "" (never None); restore None for absent so
        # downstream truthiness / log-None-dropping behaves as before.
        self.trace_id = input.trace_id or None

    def idem_key(self, step_name: str) -> str:
        return f"{self.order_id}-{step_name}-1"

    def generate_reservation_id(self) -> str:
        return f"RES-{workflow.uuid4()}"
