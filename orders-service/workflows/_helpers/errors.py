from temporalio.exceptions import ActivityError

def unwrap_activity_error(e: Exception) -> Exception:
    """Unwrap Temporal ActivityError to find the root cause."""
    cause = e
    while isinstance(cause, ActivityError) and cause.cause is not None:
        cause = cause.cause
    return cause
