from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Upper bound for a single order workflow execution. Sized above worst-case
# activity retries, two shipping create/verify cycles, and saga compensations
# (see workflows/_helpers/retry_policies.py and order/retry_policies.py).
ORDER_WORKFLOW_EXECUTION_TIMEOUT = timedelta(minutes=30)

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
