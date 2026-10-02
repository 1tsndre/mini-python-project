import datetime as dt
import decimal
import uuid
from typing import Protocol

import structlog

from common import decimals, gojson
from store_service import constant, pagination
from store_service.model import (
    CartItem,
    Order,
    OrderItem,
    OrderResponse,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Product,
)
from store_service.repository.cart_repository import CartRepository
from store_service.repository.order_repository import (
    OrderRepository,
    ProductNotFoundError,
    StockReservation,
    StockUnavailableError,
)
from store_service.repository.store_repository import StoreRepository
from store_service.service.cart_lock import CartLock
from store_service.service.cart_service import INSUFFICIENT_STOCK, lock_user_cart
from store_service.service.errors import (
    ForbiddenError,
    InsufficientStockError,
    InternalError,
    InvalidStatusError,
    NotFoundError,
    ValidationError,
)

log = structlog.get_logger()


class MessagePublisher(Protocol):
    """The part of an NSQ producer the order service needs."""

    async def publish(self, topic: str, body: bytes) -> None:
        """Raises if the message could not be published."""
        ...


class OrderService:
    def __init__(
        self,
        order_repo: OrderRepository,
        cart_repo: CartRepository,
        store_repo: StoreRepository,
        lock: CartLock | None,
        publisher: MessagePublisher | None,
    ) -> None:
        self._order_repo = order_repo
        self._cart_repo = cart_repo
        self._store_repo = store_repo
        self._lock = lock
        self._publisher = publisher

    async def checkout(self, user_id: uuid.UUID, shipping_address: str) -> list[OrderResponse]:
        # Hold the cart lock from reading the cart until it is cleared: a double-submitted checkout
        # then finds the cart already empty instead of ordering it twice, and an item added
        # meanwhile is not wiped by delete_cart.
        async with lock_user_cart(self._lock, user_id):
            try:
                cart = await self._cart_repo.get_cart(user_id)
            except Exception as e:
                raise InternalError("failed to load cart") from e
            if not cart.items:
                raise ValidationError("cart is empty")

            # A stable product order makes concurrent checkouts lock product rows in the same
            # sequence, which avoids deadlocks between them.
            items = sorted(cart.items, key=lambda item: str(item.product_id))
            reservations = [StockReservation(product_id=i.product_id, quantity=i.quantity) for i in items]

            try:
                orders = await self._order_repo.create_orders_with_stock(
                    reservations, lambda reserved: build_orders_by_store(user_id, shipping_address, items, reserved)
                )
            except StockUnavailableError as e:
                raise InsufficientStockError(f"{INSUFFICIENT_STOCK} for product {e.product_name}") from e
            except ProductNotFoundError as e:
                raise NotFoundError(str(e)) from e
            except Exception as e:
                log.error("failed to create orders", error=str(e))
                raise InternalError("failed to process checkout") from e

            try:
                await self._cart_repo.delete_cart(user_id)
            except Exception as e:  # noqa: BLE001
                log.error("failed to clear cart after checkout", error=str(e), user_id=str(user_id))

            # A failed publish is not fatal: the order stays pending and the payment retry worker
            # republishes it later.
            for order in orders:
                await self._publish_order_created(order)

            log.info("orders created", user_id=str(user_id), count=len(orders))

            return [order.to_response() for order in orders]

    async def _publish_order_created(self, order: Order) -> bool:
        if self._publisher is None:
            return False

        msg = gojson.marshal(
            {
                "order_id": str(order.id),
                "user_id": str(order.user_id),
                "total_amount": decimals.format(order.total_amount),
            }
        )
        try:
            await self._publisher.publish(constant.TOPIC_ORDER_CREATED, msg)
        except Exception as e:  # noqa: BLE001
            log.error("failed to publish order.created", error=str(e), order_id=str(order.id))
            return False
        return True

    async def retry_pending_payments(self, older_than: dt.timedelta, limit: int) -> int:
        """Republishes order.created for orders still pending after older_than, covering publishes
        that failed or were lost. Returns how many orders were republished.
        """
        orders = await self._order_repo.find_stale_pending(dt.datetime.now().astimezone() - older_than, limit)

        published = 0
        for order in orders:
            if await self._publish_order_created(order):
                published += 1
        return published

    async def get_orders(self, user_id: uuid.UUID, page: int, per_page: int) -> tuple[list[OrderResponse], int]:
        page, per_page = pagination.normalize(page, per_page)

        try:
            orders, total = await self._order_repo.find_by_user_id(user_id, page, per_page)
        except Exception as e:
            raise InternalError("failed to fetch orders") from e

        return [o.to_response() for o in orders], total

    async def get_order_by_id(self, user_id: uuid.UUID, order_id: uuid.UUID) -> OrderResponse:
        order = await self._find_order(order_id)

        if order.user_id != user_id:
            raise ForbiddenError("forbidden")

        return order.to_response()

    async def cancel_order(self, user_id: uuid.UUID, order_id: uuid.UUID) -> None:
        order = await self._find_order(order_id)

        if order.user_id != user_id:
            raise ForbiddenError("forbidden")

        if not order.status.is_cancellable:
            raise InvalidStatusError(f"cannot cancel order with status {order.status}")

        try:
            cancelled = await self._order_repo.cancel_and_restock(order_id, order.status)
        except Exception as e:
            log.error("failed to cancel order", error=str(e), order_id=str(order_id))
            raise InternalError("failed to cancel order") from e
        if not cancelled:
            raise InvalidStatusError("cannot cancel order, status changed")

        log.info("order cancelled", order_id=str(order_id))

    async def update_order_status(self, seller_id: uuid.UUID, order_id: uuid.UUID, status: str) -> None:
        order = await self._find_order(order_id)

        allowed = order.status.transitions
        if not allowed:
            raise InvalidStatusError(f"cannot transition from status {order.status}: invalid status transition")
        if status not in allowed:
            raise InvalidStatusError(f"invalid status transition from {order.status} to {status}")
        next_status = constant.OrderStatus(status)

        try:
            store = await self._store_repo.find_by_user_id(seller_id)
        except Exception as e:
            raise NotFoundError("store not found") from e

        if order.store_id != store.id:
            raise ForbiddenError("forbidden: order does not belong to your store")

        try:
            updated = await self._order_repo.update_status_if_current(order_id, order.status, next_status)
        except Exception as e:
            log.error("failed to update order status", error=str(e))
            raise InternalError("failed to update order status") from e
        if not updated:
            raise InvalidStatusError("cannot update order, status changed")

    async def get_seller_orders(self, user_id: uuid.UUID, page: int, per_page: int) -> tuple[list[OrderResponse], int]:
        page, per_page = pagination.normalize(page, per_page)

        try:
            store = await self._store_repo.find_by_user_id(user_id)
        except Exception as e:
            raise NotFoundError("store not found") from e

        try:
            orders, total = await self._order_repo.find_by_store_id(store.id, page, per_page)
        except Exception as e:
            raise InternalError("failed to fetch orders") from e

        return [o.to_response() for o in orders], total

    async def process_payment_result(self, order_id: uuid.UUID, success: bool) -> None:
        """Applies a payment result from the payment service; a result for an order that is no
        longer pending is ignored. Raises when the update failed, so the message is retried.
        """
        if success:
            try:
                paid = await self._order_repo.mark_payment_succeeded(order_id)
            except Exception as e:
                log.error("failed to mark order as paid", error=str(e), order_id=str(order_id))
                raise
            if not paid:
                log.warning("ignoring payment success for non-pending order", order_id=str(order_id))
                return
            log.info("payment success", order_id=str(order_id))
            return

        try:
            cancelled = await self._order_repo.mark_payment_failed(order_id)
        except Exception as e:
            log.error("failed to cancel order after payment failure", error=str(e), order_id=str(order_id))
            raise
        if not cancelled:
            log.warning("ignoring payment failure for non-pending order", order_id=str(order_id))
            return
        log.info("payment failed, order cancelled", order_id=str(order_id))

    async def _find_order(self, order_id: uuid.UUID) -> Order:
        try:
            return await self._order_repo.find_by_id(order_id)
        except Exception as e:
            raise NotFoundError("order not found") from e


def build_orders_by_store(
    user_id: uuid.UUID, shipping_address: str, items: list[CartItem], reserved: list[Product]
) -> list[Order]:
    """Splits the checked-out items into one pending order per store, priced at the reserved
    products' current price. items and reserved must be in the same order.
    """
    orders_by_store: dict[uuid.UUID, Order] = {}

    for i, product in enumerate(reserved):
        order = orders_by_store.get(product.store_id)
        if order is None:
            order = Order(
                user_id=user_id,
                store_id=product.store_id,
                status=constant.OrderStatus.PENDING,
                total_amount=decimal.Decimal(0),
                shipping_address=shipping_address,
            )
            orders_by_store[product.store_id] = order

        quantity = items[i].quantity
        order.order_items.append(OrderItem(product_id=product.id, quantity=quantity, price=product.price))
        order.total_amount += product.price * quantity

    for order in orders_by_store.values():
        order.payment = Payment(method=PaymentMethod.MOCK, status=PaymentStatus.PENDING, amount=order.total_amount)
    return list(orders_by_store.values())
