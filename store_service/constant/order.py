import datetime as dt
from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PROCESSING = "processing"
    SHIPPING = "shipping"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def is_cancellable(self) -> bool:
        """Whether a buyer may still cancel an order in this status."""
        return self in (OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.PROCESSING)

    @property
    def transitions(self) -> tuple["OrderStatus", ...]:
        """The statuses a seller may move an order to from this one."""
        return _TRANSITIONS.get(self, ())


_TRANSITIONS = {
    OrderStatus.PAID: (OrderStatus.PROCESSING,),
    OrderStatus.PROCESSING: (OrderStatus.SHIPPING,),
    OrderStatus.SHIPPING: (OrderStatus.SHIPPED,),
    OrderStatus.SHIPPED: (OrderStatus.COMPLETED,),
}

# How often pending orders are checked for a missing payment result.
PAYMENT_RETRY_INTERVAL = dt.timedelta(minutes=1)
# How long an order may stay pending before order.created is republished.
PAYMENT_RETRY_AFTER = dt.timedelta(minutes=2)
# How many orders are republished per check at most.
PAYMENT_RETRY_BATCH_SIZE = 100
