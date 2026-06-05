from datetime import timedelta

from temporalio.common import RetryPolicy

# shipping create: 1 attempt per cycle (controlled by workflow loop)
SHIPPING = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=4),
    maximum_attempts=1,
)

# shipping verify: few attempts
VERIFY_SHIPMENT = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=4),
    maximum_attempts=3,
)
