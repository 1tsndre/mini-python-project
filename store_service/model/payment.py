import datetime as dt
import decimal
import uuid
from dataclasses import dataclass
from enum import StrEnum

from store_service.model.zero import NIL_UUID, ZERO_DECIMAL, ZERO_TIME


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    # A payment that was still pending when the buyer cancelled the order.
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    MOCK = "mock"


@dataclass
class Payment:
    id: uuid.UUID = NIL_UUID
    order_id: uuid.UUID = NIL_UUID
    method: PaymentMethod = PaymentMethod.MOCK
    status: PaymentStatus = PaymentStatus.PENDING
    amount: decimal.Decimal = ZERO_DECIMAL
    paid_at: dt.datetime | None = None
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    def to_response(self) -> "PaymentResponse":
        return PaymentResponse(
            id=self.id,
            order_id=self.order_id,
            method=self.method,
            status=self.status,
            amount=self.amount,
            paid_at=self.paid_at,
            created_at=self.created_at,
        )


@dataclass
class PaymentResponse:
    id: uuid.UUID
    order_id: uuid.UUID
    method: PaymentMethod
    status: PaymentStatus
    amount: decimal.Decimal
    paid_at: dt.datetime | None
    created_at: dt.datetime


@dataclass
class PaymentStatusResponse:
    order_id: str
    payment_id: str
    status: str
    amount: str
    method: str
