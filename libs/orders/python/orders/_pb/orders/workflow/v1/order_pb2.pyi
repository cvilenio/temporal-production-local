from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class OrderWorkflowInput(_message.Message):
    __slots__ = ()
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_AUTHORIZATION_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_MINOR_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    item_id: str
    quantity: int
    user_id: str
    address: str
    payment_authorization_id: str
    amount_minor: int
    trace_id: str
    def __init__(self, order_id: _Optional[str] = ..., item_id: _Optional[str] = ..., quantity: _Optional[int] = ..., user_id: _Optional[str] = ..., address: _Optional[str] = ..., payment_authorization_id: _Optional[str] = ..., amount_minor: _Optional[int] = ..., trace_id: _Optional[str] = ...) -> None: ...

class OrderWorkflowResult(_message.Message):
    __slots__ = ()
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    TRACKING_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    status: str
    order_id: str
    tracking_id: str
    trace_id: str
    def __init__(self, status: _Optional[str] = ..., order_id: _Optional[str] = ..., tracking_id: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...
