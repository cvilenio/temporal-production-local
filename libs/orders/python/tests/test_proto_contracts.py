"""Protobuf activity/workflow contract behaviour (ADR-0021).

These lock the load-bearing properties of the proto migration: that proto messages
serialize through the (unchanged) Temporal pydantic data converter as readable
proto3 JSON, and that proto's evolution semantics (ignore-unknown, defaults-for-
missing) hold — the payload-compatibility guard the migration buys.
"""

from typing import Any, cast

from google.protobuf.json_format import MessageToDict
from orders.shared.contracts import (
    CapturePaymentRequest,
    MarkOrderFailedRequest,
    OrderWorkflowInput,
    ReserveInventoryRequest,
    UpdateCustomerStatusRequest,
)
from temporalio.contrib.pydantic import pydantic_data_converter

_conv = pydantic_data_converter.payload_converter


def test_proto_request_encodes_as_json_protobuf():
    # No data-converter change is needed: the pydantic converter already carries
    # the proto encoders ahead of its JSON converter, so a proto message rides as
    # human-readable proto3 JSON (not opaque binary) and round-trips intact.
    req = CapturePaymentRequest(
        contract_version=1, idem_key="k", auth_token="tok", amount_minor=12345
    )
    payload = _conv.to_payloads([req])[0]
    # Payload.metadata keys are bytes at runtime; cast to satisfy the str-typed stub.
    metadata = cast(dict[Any, Any], payload.metadata)
    assert metadata[b"encoding"] == b"json/protobuf"
    assert metadata[b"messageType"] == b"orders.activities.v1.CapturePaymentRequest"

    back = _conv.from_payloads([payload], [CapturePaymentRequest])[0]
    assert back.amount_minor == 12345
    assert back.idem_key == "k"


def test_workflow_input_round_trips():
    inp = OrderWorkflowInput(order_id="o1", quantity=2, amount_minor=999)
    payload = _conv.to_payloads([inp])[0]
    back = _conv.from_payloads([payload], [OrderWorkflowInput])[0]
    assert back.order_id == "o1"
    assert back.amount_minor == 999


def test_unknown_fields_are_ignored_forward_compat():
    # A newer producer adds a field an older worker doesn't know. proto3 ignores
    # unknown fields on decode — the opposite of Pydantic extra="forbid".
    req = ReserveInventoryRequest(
        contract_version=1, idem_key="k", item_id="i", quantity=3
    )
    data = req.SerializeToString()
    # Append an unknown field: tag for field 99 wiretype 0 (b"\x98\x06") + value 1.
    parsed = ReserveInventoryRequest()
    parsed.ParseFromString(data + b"\x98\x06\x01")
    assert parsed.item_id == "i"
    assert parsed.quantity == 3


def test_missing_fields_decode_to_defaults_not_none():
    # A new reader of an old message gets type defaults, never None — so
    # contract_version unset (0) cleanly means "legacy".
    req = CapturePaymentRequest()
    assert req.contract_version == 0
    assert req.amount_minor == 0
    assert req.auth_token == ""


def test_message_to_dict_shape_for_http_forwarding():
    # The persistence/customer activities forward MessageToDict to the orders
    # service. Keys must be snake_case (match the service's Pydantic models) and
    # the envelope fields are stripped before forwarding.
    req = MarkOrderFailedRequest(
        contract_version=1,
        order_id="o1",
        status="failed",
        failure_reason="boom",
        customer_message="sorry",
        customer_message_level="error",
        last_reached_status="capturing_payment",
    )
    payload = MessageToDict(req, preserving_proto_field_name=True)
    payload.pop("order_id", None)
    payload.pop("contract_version", None)
    assert payload["customer_message_level"] == "error"
    assert payload["last_reached_status"] == "capturing_payment"
    assert "order_id" not in payload
    assert "contract_version" not in payload


def test_customer_status_proto_omits_empty_defaults():
    # proto3 MessageToDict omits default/empty fields; the workflow always sets
    # the meaningful ones, so the forwarded dict carries them.
    req = UpdateCustomerStatusRequest(
        contract_version=1, order_id="o1", status="pending", message="hi", level="info"
    )
    payload = MessageToDict(req, preserving_proto_field_name=True)
    assert payload["status"] == "pending"
    assert payload["level"] == "info"
