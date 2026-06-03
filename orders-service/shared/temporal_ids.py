from enum import StrEnum


class TaskQueue(StrEnum):
    ORDERS_WORKFLOW = "orders-workflow-task-queue"
    ORDERS_ACTIVITY = "orders-activity-task-queue"


class ActivityName(StrEnum):
    # External side-effects
    RESERVE_INVENTORY = "reserve_inventory"
    CREATE_SHIPMENT = "create_shipment"
    CAPTURE_PAYMENT = "capture_payment"
    VERIFY_SHIPMENT_STATUS = "verify_shipment_status"
    RELEASE_INVENTORY = "release_inventory"
    CANCEL_SHIPMENT = "cancel_shipment"
    REFUND_PAYMENT = "refund_payment"
    # Persistence
    CREATE_ORDER_RECORD = "create_order_record"
    PERSIST_INVENTORY_RESERVATION = "persist_inventory_reservation"
    PERSIST_SHIPMENT = "persist_shipment"
    PERSIST_PAYMENT_CAPTURE = "persist_payment_capture"
    MARK_ORDER_FAILED = "mark_order_failed"
    FINALIZE_ORDER = "finalize_order"
    # Customer
    UPDATE_CUSTOMER_STATUS = "update_customer_status"


class SignalName(StrEnum):
    CANCEL_ORDER = "cancel_order"


class SearchAttribute(StrEnum):
    ORDER_ID = "OrderId"
    ORDER_STATUS = "OrderStatus"
    TRACE_ID = "TraceId"
