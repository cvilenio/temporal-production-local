from datetime import timedelta
from temporalio.common import RetryPolicy

# standard persistence retry: many attempts, aggressive backoff
PERSISTENCE = RetryPolicy(
    initial_interval=timedelta(milliseconds=100),
    backoff_coefficient=2.0,
    maximum_attempts=10,
)

# standard external call retry: balanced backoff
EXTERNAL_CALL = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=5,
)

# notification retry: short attempts
NOTIFY = RetryPolicy(
    initial_interval=timedelta(milliseconds=500),
    backoff_coefficient=2.0,
    maximum_attempts=3,
)

# compensation retry: high reliability
COMPENSATION = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=5,
)
