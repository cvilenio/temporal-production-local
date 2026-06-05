from typing import Literal

from pydantic import BaseModel, ConfigDict

# Terminal status reported on the workflow result. Shared so the workflow,
# terminal config, and result model stay in sync.
OrderResultStatus = Literal["Success", "Cancelled", "Failed - Shipping"]


class OrderWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    item_id: str
    quantity: int
    user_id: str
    address: str
    payment_authorization_id: str
    amount: float
    trace_id: str | None = None


class OrderWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OrderResultStatus
    order_id: str
    tracking_id: str | None = None
    trace_id: str | None = None
