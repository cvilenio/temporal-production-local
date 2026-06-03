from enum import StrEnum
from dataclasses import dataclass
from shared.models import OrderStatus

class TerminalReason(StrEnum):
    SHIPPING_UNRECOVERABLE = "shipping_unrecoverable"
    CANCELLED_BY_USER = "cancelled_by_user"

@dataclass
class TerminalConfig:
    clean_status: OrderStatus
    message: str
    return_string: str
    level: str = "warn"

TERMINAL_CONFIG = {
    TerminalReason.SHIPPING_UNRECOVERABLE: TerminalConfig(
        clean_status=OrderStatus.SHIPPING_FAILED,
        message="Unfortunately, we are unable to complete your order at this time. Your order has been cancelled and a $10 store credit has been applied toward your next purchase.",
        return_string="Failed - Shipping",
    ),
    TerminalReason.CANCELLED_BY_USER: TerminalConfig(
        clean_status=OrderStatus.CANCELLED,
        message="Your order has been cancelled.",
        return_string="Cancelled",
    ),
}
