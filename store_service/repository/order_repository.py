import contextlib
import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from store_service import constant, pagination
from store_service.model import (
    Order,
    OrderItem,
    Payment,
    PaymentStatus,
    Product,
)
from store_service.repository.caches import Cache
from store_service.repository.databases import Database, Executor
from store_service.repository.errors import RecordNotFoundError
from store_service.repository.product_repository import PRODUCT_COLUMNS
from store_service.repository.rows import scan, scan_into

ORDER_COLUMNS = "id, user_id, store_id, status, total_amount, shipping_address, created_at, updated_at"
ORDER_ITEM_COLUMNS = "id, order_id, product_id, quantity, price, created_at"
PAYMENT_COLUMNS = "id, order_id, method, status, amount, paid_at, created_at, updated_at"


@dataclass(frozen=True)
class StockReservation:
    product_id: uuid.UUID
    quantity: int


class ProductNotFoundError(Exception):
    def __init__(self, product_id: uuid.UUID) -> None:
        super().__init__(f"product {product_id} not found")
        self.product_id = product_id


class StockUnavailableError(Exception):
    def __init__(self, product_id: uuid.UUID, product_name: str) -> None:
        super().__init__(f"insufficient stock for product {product_name}")
        self.product_id = product_id
        self.product_name = product_name


class OrderRepository:
    def __init__(self, db: Database, cache: Cache) -> None:
        self._db = db
        self._cache = cache

    async def create_orders_with_stock(
        self, reservations: list[StockReservation], build: Callable[[list[Product]], list[Order]]
    ) -> list[Order]:
        """Decrements stock for every reservation and inserts the orders returned by build in a single
        transaction. build receives the reserved products (post-decrement, in reservation order).
        Nothing is written unless every reservation succeeds.
        """
        async with self._db.transaction() as tx:
            reserved: list[Product] = []
            for res in reservations:
                # The conditional decrement takes a row lock, so concurrent checkouts of the same
                # product serialize here and can never drive stock below zero.
                row = await tx.fetchrow(
                    """
				UPDATE products
				SET stock = stock - $1, updated_at = NOW()
				WHERE id = $2 AND stock >= $1
				RETURNING """
                    + PRODUCT_COLUMNS,
                    res.quantity,
                    res.product_id,
                )
                if row is None:
                    raise await _reservation_error(tx, res.product_id)
                reserved.append(scan(Product, row))

            orders = build(reserved)
            for order in orders:
                await _insert_order(tx, order)

        for res in reservations:
            await self._invalidate_product(res.product_id)
        return orders

    async def find_by_id(self, order_id: uuid.UUID) -> Order:
        row = await self._db.fetchrow("SELECT " + ORDER_COLUMNS + " FROM orders WHERE id = $1", order_id)
        if row is None:
            raise RecordNotFoundError()
        orders = [scan(Order, row)]
        await self._load_details(orders)
        return orders[0]

    async def find_by_user_id(self, user_id: uuid.UUID, page: int, per_page: int) -> tuple[list[Order], int]:
        return await self._find_page("user_id", user_id, page, per_page)

    async def find_by_store_id(self, store_id: uuid.UUID, page: int, per_page: int) -> tuple[list[Order], int]:
        return await self._find_page("store_id", store_id, page, per_page)

    async def _find_page(self, column: str, value: uuid.UUID, page: int, per_page: int) -> tuple[list[Order], int]:
        """A page of the orders whose column equals value, newest first, with their items and payments.
        column is a literal from the callers above, never user input.
        """
        total = await self._db.fetchval("SELECT COUNT(*) FROM orders WHERE " + column + " = $1", value)

        rows = await self._db.fetch(
            """
		SELECT """
            + ORDER_COLUMNS
            + """
		FROM orders
		WHERE """
            + column
            + """ = $1
		ORDER BY created_at DESC, id DESC
		LIMIT $2 OFFSET $3""",
            value,
            per_page,
            pagination.offset(page, per_page),
        )
        orders = [scan(Order, row) for row in rows]
        await self._load_details(orders)
        return orders, total

    async def _load_details(self, orders: list[Order]) -> None:
        """Fills in the items and payment of each order, with one query for all items and one for all
        payments.
        """
        if not orders:
            return
        ids = [order.id for order in orders]
        by_id = {order.id: order for order in orders}

        items = await self._db.fetch(
            "SELECT " + ORDER_ITEM_COLUMNS + " FROM order_items WHERE order_id = ANY($1::uuid[]) ORDER BY product_id",
            ids,
        )
        for row in items:
            item = scan(OrderItem, row)
            by_id[item.order_id].order_items.append(item)

        payments = await self._db.fetch(
            "SELECT " + PAYMENT_COLUMNS + " FROM payments WHERE order_id = ANY($1::uuid[])", ids
        )
        for row in payments:
            payment = scan(Payment, row)
            by_id[payment.order_id].payment = payment

    async def find_stale_pending(self, created_before: dt.datetime, limit: int) -> list[Order]:
        rows = await self._db.fetch(
            """
		SELECT """
            + ORDER_COLUMNS
            + """
		FROM orders
		WHERE status = $1 AND created_at < $2
		ORDER BY created_at ASC
		LIMIT $3""",
            constant.OrderStatus.PENDING,
            created_before,
            limit,
        )
        return [scan(Order, row) for row in rows]

    async def update_status_if_current(
        self, order_id: uuid.UUID, from_status: constant.OrderStatus, to_status: constant.OrderStatus
    ) -> bool:
        changed = await self._db.execute(
            "UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2 AND status = $3",
            to_status,
            order_id,
            from_status,
        )
        return changed > 0

    async def cancel_and_restock(self, order_id: uuid.UUID, from_status: constant.OrderStatus) -> bool:
        """Moves the order from from_status to cancelled, returns its items to stock and closes a
        still-pending payment, all in one transaction. Reports False if the order was no longer in
        from_status.
        """
        return await self._cancel_and_restock(order_id, from_status, PaymentStatus.CANCELLED)

    async def mark_payment_failed(self, order_id: uuid.UUID) -> bool:
        """Cancels a pending order, restocks its items and records the payment as failed. Reports
        False if the order was no longer pending.
        """
        return await self._cancel_and_restock(order_id, constant.OrderStatus.PENDING, PaymentStatus.FAILED)

    async def _cancel_and_restock(
        self, order_id: uuid.UUID, from_status: constant.OrderStatus, pending_payment_status: PaymentStatus
    ) -> bool:
        items: list[OrderItem] = []
        async with self._db.transaction() as tx:
            changed = await tx.execute(
                "UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2 AND status = $3",
                constant.OrderStatus.CANCELLED,
                order_id,
                from_status,
            )
            if changed == 0:
                return False

            # Restock in product_id order, the same order checkout reserves in, so a concurrent
            # checkout and cancel cannot deadlock on product row locks.
            rows = await tx.fetch(
                "SELECT " + ORDER_ITEM_COLUMNS + " FROM order_items WHERE order_id = $1 ORDER BY product_id", order_id
            )
            items = [scan(OrderItem, row) for row in rows]
            for item in items:
                await tx.execute(
                    "UPDATE products SET stock = stock + $1, updated_at = NOW() WHERE id = $2",
                    item.quantity,
                    item.product_id,
                )

            await tx.execute(
                "UPDATE payments SET status = $1, updated_at = NOW() WHERE order_id = $2 AND status = $3",
                pending_payment_status,
                order_id,
                PaymentStatus.PENDING,
            )

        for item in items:
            await self._invalidate_product(item.product_id)
        return True

    async def mark_payment_succeeded(self, order_id: uuid.UUID) -> bool:
        """Moves a pending order to paid and records the payment as successful. Reports False if the
        order was no longer pending.
        """
        async with self._db.transaction() as tx:
            changed = await tx.execute(
                "UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2 AND status = $3",
                constant.OrderStatus.PAID,
                order_id,
                constant.OrderStatus.PENDING,
            )
            if changed == 0:
                return False

            await tx.execute(
                """
			UPDATE payments
			SET status = $1, paid_at = NOW(), updated_at = NOW()
			WHERE order_id = $2 AND status = $3""",
                PaymentStatus.SUCCESS,
                order_id,
                PaymentStatus.PENDING,
            )
        return True

    async def _invalidate_product(self, product_id: uuid.UUID) -> None:
        with contextlib.suppress(Exception):
            await self._cache.delete(constant.KEY_PRODUCT.format(product_id))


async def _reservation_error(tx: Executor, product_id: uuid.UUID) -> Exception:
    """Explains why a conditional stock decrement matched no row."""
    name = await tx.fetchval("SELECT name FROM products WHERE id = $1", product_id)
    if name is None:
        return ProductNotFoundError(product_id)
    return StockUnavailableError(product_id, name)


async def _insert_order(tx: Executor, order: Order) -> None:
    """Inserts the order with its items and payment, and reads back the IDs and timestamps the
    database assigns.
    """
    row = await tx.fetchrow(
        """
		INSERT INTO orders (user_id, store_id, status, total_amount, shipping_address)
		VALUES ($1, $2, $3, $4, $5)
		RETURNING """
        + ORDER_COLUMNS,
        order.user_id,
        order.store_id,
        order.status,
        order.total_amount,
        order.shipping_address,
    )
    scan_into(order, row)

    for item in order.order_items:
        row = await tx.fetchrow(
            """
			INSERT INTO order_items (order_id, product_id, quantity, price)
			VALUES ($1, $2, $3, $4)
			RETURNING """
            + ORDER_ITEM_COLUMNS,
            order.id,
            item.product_id,
            item.quantity,
            item.price,
        )
        scan_into(item, row)

    if order.payment is None:
        return
    row = await tx.fetchrow(
        """
		INSERT INTO payments (order_id, method, status, amount)
		VALUES ($1, $2, $3, $4)
		RETURNING """
        + PAYMENT_COLUMNS,
        order.id,
        order.payment.method,
        order.payment.status,
        order.payment.amount,
    )
    scan_into(order.payment, row)
