from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ReserveInventoryRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    item_id: str
    quantity: int
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ..., item_id: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class CreateShipmentRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    address: str
    order_id: str
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ..., address: _Optional[str] = ..., order_id: _Optional[str] = ...) -> None: ...

class ShipmentCreatedResult(_message.Message):
    __slots__ = ()
    TRACKING_ID_FIELD_NUMBER: _ClassVar[int]
    tracking_id: str
    def __init__(self, tracking_id: _Optional[str] = ...) -> None: ...

class VerifyShipmentRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ...) -> None: ...

class CapturePaymentRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    AUTH_TOKEN_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_MINOR_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    auth_token: str
    amount_minor: int
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ..., auth_token: _Optional[str] = ..., amount_minor: _Optional[int] = ...) -> None: ...

class ReleaseInventoryRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    reservation_id: str
    item_id: str
    quantity: int
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ..., reservation_id: _Optional[str] = ..., item_id: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class CancelShipmentRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    TRACKING_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    tracking_id: str
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ..., tracking_id: _Optional[str] = ...) -> None: ...

class RefundPaymentRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEM_KEY_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_MINOR_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    idem_key: str
    capture_id: str
    amount_minor: int
    def __init__(self, contract_version: _Optional[int] = ..., idem_key: _Optional[str] = ..., capture_id: _Optional[str] = ..., amount_minor: _Optional[int] = ...) -> None: ...

class CreateOrderRecordRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_AUTHORIZATION_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_MINOR_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    WORKFLOW_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    item_id: str
    quantity: int
    user_id: str
    address: str
    payment_authorization_id: str
    amount_minor: int
    trace_id: str
    workflow_id: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ..., item_id: _Optional[str] = ..., quantity: _Optional[int] = ..., user_id: _Optional[str] = ..., address: _Optional[str] = ..., payment_authorization_id: _Optional[str] = ..., amount_minor: _Optional[int] = ..., trace_id: _Optional[str] = ..., workflow_id: _Optional[str] = ...) -> None: ...

class PersistInventoryReservationRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    reservation_id: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ..., reservation_id: _Optional[str] = ...) -> None: ...

class PersistShipmentRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    TRACKING_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    tracking_id: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ..., tracking_id: _Optional[str] = ...) -> None: ...

class PersistPaymentCaptureRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    capture_id: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ..., capture_id: _Optional[str] = ...) -> None: ...

class MarkOrderFailedRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FAILURE_REASON_FIELD_NUMBER: _ClassVar[int]
    CUSTOMER_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CUSTOMER_MESSAGE_LEVEL_FIELD_NUMBER: _ClassVar[int]
    LAST_REACHED_STATUS_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    status: str
    failure_reason: str
    customer_message: str
    customer_message_level: str
    last_reached_status: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ..., status: _Optional[str] = ..., failure_reason: _Optional[str] = ..., customer_message: _Optional[str] = ..., customer_message_level: _Optional[str] = ..., last_reached_status: _Optional[str] = ...) -> None: ...

class FinalizeOrderRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ...) -> None: ...

class UpdateCustomerStatusRequest(_message.Message):
    __slots__ = ()
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    contract_version: int
    order_id: str
    status: str
    message: str
    level: str
    def __init__(self, contract_version: _Optional[int] = ..., order_id: _Optional[str] = ..., status: _Optional[str] = ..., message: _Optional[str] = ..., level: _Optional[str] = ...) -> None: ...
