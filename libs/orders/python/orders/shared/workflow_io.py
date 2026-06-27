from datetime import timedelta
from typing import Literal

from orders.shared.contracts import OrderWorkflowInput, OrderWorkflowResult

# Upper bound for a single order workflow execution. Sized above worst-case
# activity retries, two shipping create/verify cycles, and saga compensations
# (see workflows/_helpers/retry_policies.py and order/retry_policies.py).
ORDER_WORKFLOW_EXECUTION_TIMEOUT = timedelta(minutes=30)

# Terminal status reported on the workflow result. Shared so the workflow,
# terminal config, and result model stay in sync. Carried as a plain string on
# the OrderWorkflowResult proto (ADR-0021: status stays a string on the wire).
OrderResultStatus = Literal["Success", "Cancelled", "Failed - Shipping"]

# OrderWorkflowInput / OrderWorkflowResult are now protobuf messages (generated
# from libs/orders/proto). Money is carried as amount_minor (cents).
__all__ = [
    "ORDER_WORKFLOW_EXECUTION_TIMEOUT",
    "OrderResultStatus",
    "OrderWorkflowInput",
    "OrderWorkflowResult",
]
