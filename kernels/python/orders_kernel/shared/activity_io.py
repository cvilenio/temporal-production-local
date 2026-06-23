from orders_kernel.shared.models import OrderStatus
from pydantic import BaseModel, ConfigDict


class ActivityBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idem_key: str


# External Activities
class ReserveInventoryRequest(ActivityBase):
    item_id: str
    quantity: int


class CreateShipmentRequest(ActivityBase):
    address: str
    order_id: str


class ShipmentCreatedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tracking_id: str


class VerifyShipmentRequest(ActivityBase):
    pass


class CapturePaymentRequest(ActivityBase):
    auth_token: str
    amount: float


class ReleaseInventoryRequest(ActivityBase):
    reservation_id: str
    item_id: str
    quantity: int


class CancelShipmentRequest(ActivityBase):
    tracking_id: str


class RefundPaymentRequest(ActivityBase):
    capture_id: str
    amount: float


# Persistence Activities
class CreateOrderRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    item_id: str
    quantity: int
    user_id: str
    address: str
    payment_authorization_id: str
    amount: float
    trace_id: str | None = None
    workflow_id: str


class PersistInventoryReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    reservation_id: str


class PersistShipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    tracking_id: str


class PersistPaymentCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    capture_id: str


class MarkOrderFailedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    status: OrderStatus
    failure_reason: str | None = None
    customer_message: str | None = None
    customer_message_level: str = "error"
    last_reached_status: OrderStatus | None = None


class FinalizeOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str


# Customer Messaging
class UpdateCustomerStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    status: OrderStatus
    message: str
    level: str = "info"
