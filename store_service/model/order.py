import datetime as dt
import decimal
import uuid
from dataclasses import dataclass, field

from common import gojson
from store_service.constant import OrderStatus
from store_service.model.payment import Payment, PaymentResponse
from store_service.model.zero import NIL_UUID, ZERO_DECIMAL, ZERO_TIME


@dataclass
class OrderItem:
    id: uuid.UUID = NIL_UUID
    order_id: uuid.UUID = NIL_UUID
    product_id: uuid.UUID = NIL_UUID
    quantity: int = 0
    price: decimal.Decimal = ZERO_DECIMAL
    created_at: dt.datetime = ZERO_TIME


@dataclass
class Order:
    id: uuid.UUID = NIL_UUID
    user_id: uuid.UUID = NIL_UUID
    store_id: uuid.UUID = NIL_UUID
    status: OrderStatus = OrderStatus.PENDING
    total_amount: decimal.Decimal = ZERO_DECIMAL
    shipping_address: str = ""
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    # Stored in their own tables; the repository loads and inserts them.
    order_items: list[OrderItem] = field(default_factory=list, metadata=gojson.OMIT_EMPTY)
    payment: Payment | None = field(default=None, metadata=gojson.OMIT_NIL)

    def to_response(self) -> "OrderResponse":
        items = None
        for item in self.order_items:
            if items is None:
                items = []
            items.append(
                OrderItemResponse(
                    id=item.id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    price=item.price,
                    subtotal=item.price * item.quantity,
                )
            )
        return OrderResponse(
            id=self.id,
            user_id=self.user_id,
            store_id=self.store_id,
            status=self.status,
            total_amount=self.total_amount,
            shipping_address=self.shipping_address,
            items=items,
            payment=self.payment.to_response() if self.payment is not None else None,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class UpdateOrderStatusRequest:
    status: str = ""


@dataclass
class CheckoutRequest:
    shipping_address: str = ""


@dataclass
class OrderItemResponse:
    id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    price: decimal.Decimal
    subtotal: decimal.Decimal


@dataclass
class OrderResponse:
    id: uuid.UUID
    user_id: uuid.UUID
    store_id: uuid.UUID
    status: OrderStatus
    total_amount: decimal.Decimal
    shipping_address: str
    # null when the order has no items, as Go encodes a nil slice.
    items: list[OrderItemResponse] | None
    payment: PaymentResponse | None = field(metadata=gojson.OMIT_NIL)
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME
