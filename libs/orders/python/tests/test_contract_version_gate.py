"""Version-in-command gate behaviour (ADR-0021, axis 3)."""

import pytest
from orders.activities.contract_gate import gate
from orders.shared.contract_version import CONTRACT_VERSION
from orders.shared.contracts import CapturePaymentRequest
from orders.shared.errors import ErrorType
from temporalio.exceptions import ApplicationError


def test_gate_accepts_current_version():
    req = CapturePaymentRequest(contract_version=CONTRACT_VERSION)
    assert gate(req) == CONTRACT_VERSION


def test_gate_treats_unset_version_as_v1():
    req = CapturePaymentRequest()  # contract_version defaults to 0
    assert gate(req, min_v=1, max_v=1) == 1


def test_gate_rejects_unsupported_version_non_retryably():
    req = CapturePaymentRequest(contract_version=99)
    with pytest.raises(ApplicationError) as exc:
        gate(req, min_v=1, max_v=1)
    assert exc.value.non_retryable is True
    assert exc.value.type == ErrorType.CONTRACT_VERSION_UNSUPPORTED


def test_gate_accepts_within_widened_range():
    # Simulates an activity that has learned to accept v2 while v1 is still live.
    req = CapturePaymentRequest(contract_version=2)
    assert gate(req, min_v=1, max_v=2) == 2
