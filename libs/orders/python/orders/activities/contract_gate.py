"""Activity-side version-in-command guard (ADR-0021, axis 3).

Lives with the activities (not in ``shared``) because it raises a Temporal
``ApplicationError`` — an activity concern. The emit-side version constant is the
dependency-free ``orders.shared.contract_version.CONTRACT_VERSION``.
"""

from temporalio.exceptions import ApplicationError

from orders.shared.contract_version import CONTRACT_VERSION
from orders.shared.errors import ErrorType


def gate(req, *, min_v: int = 1, max_v: int = CONTRACT_VERSION) -> int:
    """Return the request's contract version after asserting the activity supports it.

    A missing/zero ``contract_version`` is treated as the legacy v1 contract. An
    out-of-range version fails non-retryably: a version mismatch is deterministic,
    so retrying the same payload cannot fix it — surface it for an operator/deploy.
    """
    version = req.contract_version or 1
    if not (min_v <= version <= max_v):
        raise ApplicationError(
            f"unsupported contract_version {version} (supported {min_v}..{max_v})",
            type=ErrorType.CONTRACT_VERSION_UNSUPPORTED,
            non_retryable=True,
        )
    return version
