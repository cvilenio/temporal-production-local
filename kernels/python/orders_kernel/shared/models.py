from enum import StrEnum

from pydantic import BaseModel


class OrderStatus(StrEnum):
    # Progress
    PENDING = "pending"
    RESERVING_INVENTORY = "reserving_inventory"
    INVENTORY_RESERVED = "inventory_reserved"
    CREATING_SHIPMENT = "creating_shipment"
    SHIPMENT_CREATED = "shipment_created"
    CAPTURING_PAYMENT = "capturing_payment"
    PAYMENT_CAPTURED = "payment_captured"
    FINALIZING = "finalizing"

    # Terminal — business-acceptable
    COMPLETED = "completed"
    SHIPPING_FAILED = "shipping_failed"
    CANCELLED = "cancelled"

    # Terminal — operational failure
    CANCELLED_WITH_ISSUES = "cancelled_with_issues"
    FAILED = "failed"

    @classmethod
    def terminal_statuses(cls) -> frozenset["OrderStatus"]:
        return frozenset(
            {
                cls.COMPLETED,
                cls.SHIPPING_FAILED,
                cls.CANCELLED,
                cls.CANCELLED_WITH_ISSUES,
                cls.FAILED,
            }
        )


class OrderRequest(BaseModel):
    item_id: str
    quantity: int
    user_id: str
    address: str
    payment_authorization_id: str
    amount: float
    cart_version: str
    trace_id: str | None = None


class CustomerStatusUpdate(BaseModel):
    status: str
    message: str
    level: str = "info"
    store_credit_cents: int | None = None


class InventoryReservationUpdate(BaseModel):
    reservation_id: str


class ShipmentUpdate(BaseModel):
    tracking_id: str


class PaymentCaptureUpdate(BaseModel):
    capture_id: str


class OrderFailRequest(BaseModel):
    status: str
    failure_reason: str | None = None
    customer_message: str | None = None
    customer_message_level: str = "error"
    store_credit_cents: int | None = None
    last_reached_status: str | None = None
