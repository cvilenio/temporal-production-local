"""Activity request/response contracts.

These are now protobuf messages (generated from libs/orders/proto, see
ADR-0021) rather than Pydantic models. This module re-exports them under their
historical names so activity and workflow imports are unchanged.

Construction is keyword-based, like before:
    ReserveInventoryRequest(contract_version=1, idem_key=k, item_id="x", quantity=3)

Differences from the old Pydantic models: no extra="forbid" / validation, scalar
fields default to "" / 0 (never None), and every request carries a
``contract_version`` field (the version-in-command guard — see
orders.shared.contract_version).
"""

from orders.shared.contracts import (
    CancelShipmentRequest,
    CapturePaymentRequest,
    CreateOrderRecordRequest,
    CreateShipmentRequest,
    FinalizeOrderRequest,
    MarkOrderFailedRequest,
    PersistInventoryReservationRequest,
    PersistPaymentCaptureRequest,
    PersistShipmentRequest,
    RefundPaymentRequest,
    ReleaseInventoryRequest,
    ReserveInventoryRequest,
    ShipmentCreatedResult,
    UpdateCustomerStatusRequest,
    VerifyShipmentRequest,
)

__all__ = [
    "ReserveInventoryRequest",
    "CreateShipmentRequest",
    "ShipmentCreatedResult",
    "VerifyShipmentRequest",
    "CapturePaymentRequest",
    "ReleaseInventoryRequest",
    "CancelShipmentRequest",
    "RefundPaymentRequest",
    "CreateOrderRecordRequest",
    "PersistInventoryReservationRequest",
    "PersistShipmentRequest",
    "PersistPaymentCaptureRequest",
    "MarkOrderFailedRequest",
    "FinalizeOrderRequest",
    "UpdateCustomerStatusRequest",
]
