"""Friendly re-exports of the generated protobuf activity/workflow contracts.

The generated ``*_pb2`` modules live under ``orders._pb`` (committed, see ADR-0021)
at a path that mirrors the proto package. Everything in the codebase imports the
message types from *here* so call sites stay readable and the generated layout
stays isolated — if codegen output ever moves, only this module changes.

Regenerate the underlying code with ``just proto-gen`` after editing the ``.proto``
sources in ``libs/orders/proto``.
"""

from orders._pb.orders.activities.v1 import activities_pb2
from orders._pb.orders.workflow.v1 import order_pb2

# External side-effect activities
ReserveInventoryRequest = activities_pb2.ReserveInventoryRequest
CreateShipmentRequest = activities_pb2.CreateShipmentRequest
ShipmentCreatedResult = activities_pb2.ShipmentCreatedResult
VerifyShipmentRequest = activities_pb2.VerifyShipmentRequest
CapturePaymentRequest = activities_pb2.CapturePaymentRequest
ReleaseInventoryRequest = activities_pb2.ReleaseInventoryRequest
CancelShipmentRequest = activities_pb2.CancelShipmentRequest
RefundPaymentRequest = activities_pb2.RefundPaymentRequest

# Persistence activities
CreateOrderRecordRequest = activities_pb2.CreateOrderRecordRequest
PersistInventoryReservationRequest = activities_pb2.PersistInventoryReservationRequest
PersistShipmentRequest = activities_pb2.PersistShipmentRequest
PersistPaymentCaptureRequest = activities_pb2.PersistPaymentCaptureRequest
MarkOrderFailedRequest = activities_pb2.MarkOrderFailedRequest
FinalizeOrderRequest = activities_pb2.FinalizeOrderRequest

# Customer messaging activity
UpdateCustomerStatusRequest = activities_pb2.UpdateCustomerStatusRequest

# Workflow I/O
OrderWorkflowInput = order_pb2.OrderWorkflowInput
OrderWorkflowResult = order_pb2.OrderWorkflowResult

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
    "OrderWorkflowInput",
    "OrderWorkflowResult",
]
