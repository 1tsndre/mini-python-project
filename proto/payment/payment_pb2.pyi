from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ProcessPaymentRequest(_message.Message):
    __slots__ = ("order_id", "amount", "method")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    amount: str
    method: str
    def __init__(self, order_id: _Optional[str] = ..., amount: _Optional[str] = ..., method: _Optional[str] = ...) -> None: ...

class ProcessPaymentResponse(_message.Message):
    __slots__ = ("success", "payment_id", "status", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    payment_id: str
    status: str
    message: str
    def __init__(self, success: _Optional[bool] = ..., payment_id: _Optional[str] = ..., status: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class GetPaymentStatusRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class GetPaymentStatusResponse(_message.Message):
    __slots__ = ("payment_id", "order_id", "status", "amount", "method")
    PAYMENT_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    payment_id: str
    order_id: str
    status: str
    amount: str
    method: str
    def __init__(self, payment_id: _Optional[str] = ..., order_id: _Optional[str] = ..., status: _Optional[str] = ..., amount: _Optional[str] = ..., method: _Optional[str] = ...) -> None: ...
