from temporalio import workflow

from orders_kernel.shared.workflow_io import OrderWorkflowInput


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
        self.amount = input.amount
        self.trace_id = input.trace_id

    def idem_key(self, step_name: str) -> str:
        return f"{self.order_id}-{step_name}-1"

    def generate_reservation_id(self) -> str:
        return f"RES-{workflow.uuid4()}"
