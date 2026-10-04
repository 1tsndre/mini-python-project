import datetime as dt
import decimal
import uuid
from collections.abc import Callable
from unittest.mock import ANY

import pytest

from store_service import constant
from store_service.model import Cart, CartItem, Order, OrderItem, PaymentStatus, Product, Store
from store_service.repository.cart_repository import CartRepository
from store_service.repository.order_repository import (
    OrderRepository,
    ProductNotFoundError,
    StockReservation,
    StockUnavailableError,
)
from store_service.repository.store_repository import StoreRepository
from store_service.service.errors import (
    ForbiddenError,
    InsufficientStockError,
    InternalError,
    InvalidStatusError,
    NotFoundError,
    ValidationError,
)
from store_service.service.order_service import OrderService, build_orders_by_store
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, not_found


class FakePublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.messages: list[str] = []
        self.error = error

    async def publish(self, topic: str, body: bytes) -> None:
        if self.error is not None:
            raise self.error
        self.messages.append(f"{topic} {body.decode()}")


class Repos:
    def __init__(self, ctrl: Controller) -> None:
        self.orders = ctrl.mock(OrderRepository)
        self.carts = ctrl.mock(CartRepository)
        self.stores = ctrl.mock(StoreRepository)

    def service(self, publisher: FakePublisher | None = None) -> OrderService:
        return OrderService(self.orders, self.carts, self.stores, None, publisher)


USER_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
SELLER_ID = uuid.uuid4()
STORE_ID = uuid.uuid4()
ORDER_ID = uuid.uuid4()


class TestCheckout:
    store_a, store_b = uuid.uuid4(), uuid.uuid4()
    product_a1, product_a2, product_b1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    address = "Jl. Test No. 1, Jakarta"

    def cart(self) -> Cart:
        return Cart(
            user_id=USER_ID,
            items=[
                CartItem(product_id=self.product_a1, quantity=2),
                CartItem(product_id=self.product_b1, quantity=1),
                CartItem(product_id=self.product_a2, quantity=3),
            ],
        )

    def reserve_all(self) -> Callable[[list[StockReservation], Callable[[list[Product]], list[Order]]], list[Order]]:
        """Simulates a successful stock reservation by handing the reserved products to the service's
        build callback, as the real repository does. It also checks the reservations: sorted by
        product ID (the lock order that keeps concurrent checkouts and cancels from deadlocking)
        with the cart quantities.
        """
        catalog = {
            self.product_a1: Product(id=self.product_a1, store_id=self.store_a, price=decimal.Decimal(100)),
            self.product_a2: Product(id=self.product_a2, store_id=self.store_a, price=decimal.Decimal(10)),
            self.product_b1: Product(id=self.product_b1, store_id=self.store_b, price=decimal.Decimal(50)),
        }
        want_quantity = {self.product_a1: 2, self.product_a2: 3, self.product_b1: 1}

        def reserve(reservations: list[StockReservation], build: Callable[[list[Product]], list[Order]]) -> list[Order]:
            assert {r.product_id: r.quantity for r in reservations} == want_quantity
            ids = [str(r.product_id) for r in reservations]
            assert ids == sorted(ids), "reservations must be sorted by product ID"
            orders = build([catalog[r.product_id] for r in reservations])
            for order in orders:
                order.id = uuid.uuid4()
            return orders

        return reserve

    async def test_cart_load_fails(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.carts.get_cart, USER_ID, raises=ConnectionResetError("connection reset"))

        with pytest.raises(InternalError, match="failed to load cart"):
            await repos.service(FakePublisher()).checkout(USER_ID, self.address)

    async def test_cart_is_empty(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.carts.get_cart, USER_ID, returns=Cart(user_id=USER_ID, items=[]))

        with pytest.raises(ValidationError, match="cart is empty"):
            await repos.service(FakePublisher()).checkout(USER_ID, self.address)

    async def test_success_split_into_one_order_per_store(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.carts.get_cart, USER_ID, returns=self.cart())
        ctrl.expect(repos.orders.create_orders_with_stock, ANY, ANY, does=self.reserve_all())
        ctrl.expect(repos.carts.delete_cart, USER_ID)
        publisher = FakePublisher()

        orders = await repos.service(publisher).checkout(USER_ID, self.address)

        assert len(orders) == 2
        assert len(publisher.messages) == 2
        # The message is what the Go service publishes: sorted keys, the amount as a plain string.
        order = next(o for o in orders if o.store_id == self.store_a)
        assert (
            f'order.created {{"order_id":"{order.id}","total_amount":"230","user_id":"{USER_ID}"}}'
            in publisher.messages
        )

    async def test_success_publish_failure_does_not_fail_checkout(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.carts.get_cart, USER_ID, returns=self.cart())
        ctrl.expect(repos.orders.create_orders_with_stock, ANY, ANY, does=self.reserve_all())
        ctrl.expect(repos.carts.delete_cart, USER_ID)
        publisher = FakePublisher(ConnectionError("nsq down"))

        orders = await repos.service(publisher).checkout(USER_ID, self.address)

        assert len(orders) == 2
        assert publisher.messages == []

    @pytest.mark.parametrize(
        ("error", "expected", "message"),
        [
            (
                StockUnavailableError(product_a1, "Laptop"),
                InsufficientStockError,
                "insufficient stock for product Laptop",
            ),
            (ProductNotFoundError(product_b1), NotFoundError, "not found"),
            (ConnectionResetError("connection reset"), InternalError, "failed to process checkout"),
        ],
        ids=["insufficient stock", "product no longer exists", "database failure"],
    )
    async def test_reservation_fails(
        self, ctrl: Controller, error: Exception, expected: type[Exception], message: str
    ) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.carts.get_cart, USER_ID, returns=self.cart())
        ctrl.expect(repos.orders.create_orders_with_stock, ANY, ANY, raises=error)

        with pytest.raises(expected, match=message):
            await repos.service(FakePublisher()).checkout(USER_ID, self.address)


def test_build_orders_by_store() -> None:
    store_a, store_b = uuid.uuid4(), uuid.uuid4()
    items = [
        CartItem(product_id=uuid.uuid4(), quantity=2),
        CartItem(product_id=uuid.uuid4(), quantity=1),
        CartItem(product_id=uuid.uuid4(), quantity=3),
    ]
    reserved = [
        Product(id=items[0].product_id, store_id=store_a, price=decimal.Decimal(100)),
        Product(id=items[1].product_id, store_id=store_b, price=decimal.Decimal(50)),
        Product(id=items[2].product_id, store_id=store_a, price=decimal.Decimal(10)),
    ]

    orders = build_orders_by_store(USER_ID, "addr", items, reserved)

    assert len(orders) == 2
    assert orders[0].store_id == store_a
    assert len(orders[0].order_items) == 2
    assert orders[0].total_amount == 230
    assert orders[1].store_id == store_b
    assert orders[1].total_amount == 50
    for order in orders:
        assert order.status == constant.OrderStatus.PENDING
        assert order.user_id == USER_ID
        assert order.payment is not None
        assert order.payment.status == PaymentStatus.PENDING
        assert order.payment.amount == order.total_amount


async def test_retry_pending_payments(ctrl: Controller) -> None:
    repos = Repos(ctrl)
    stale = [
        Order(id=uuid.uuid4(), user_id=uuid.uuid4(), total_amount=decimal.Decimal(10)),
        Order(id=uuid.uuid4(), user_id=uuid.uuid4(), total_amount=decimal.Decimal(20)),
    ]
    ctrl.expect(repos.orders.find_stale_pending, ANY, 50, returns=stale)
    publisher = FakePublisher()

    count = await repos.service(publisher).retry_pending_payments(dt.timedelta(minutes=2), 50)

    assert count == 2
    assert len(publisher.messages) == 2
    assert constant.TOPIC_ORDER_CREATED in publisher.messages[0]
    assert str(stale[0].id) in publisher.messages[0]


class TestGetOrders:
    @pytest.mark.parametrize(
        ("page", "per_page", "orders"),
        [
            (
                1,
                10,
                [
                    Order(id=uuid.uuid4(), user_id=USER_ID, status=constant.OrderStatus.PENDING),
                    Order(id=uuid.uuid4(), user_id=USER_ID, status=constant.OrderStatus.PAID),
                ],
            ),
            (0, 0, []),
        ],
        ids=["success", "default pagination on zero values"],
    )
    async def test_success(self, ctrl: Controller, page: int, per_page: int, orders: list[Order]) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_user_id, USER_ID, 1, 10, returns=(orders, len(orders)))

        resp, total = await repos.service().get_orders(USER_ID, page, per_page)

        assert len(resp) == len(orders)
        assert total == len(orders)

    async def test_db_error(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_user_id, USER_ID, 1, 10, raises=db_error())

        with pytest.raises(InternalError):
            await repos.service().get_orders(USER_ID, 1, 10)


class TestGetOrderByID:
    async def test_success(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, user_id=OWNER_ID, status=constant.OrderStatus.PENDING)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)

        assert (await repos.service().get_order_by_id(OWNER_ID, ORDER_ID)).id == ORDER_ID

    async def test_order_not_found(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="order not found"):
            await repos.service().get_order_by_id(OWNER_ID, ORDER_ID)

    async def test_forbidden_different_user(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=Order(id=ORDER_ID, user_id=OWNER_ID))

        with pytest.raises(ForbiddenError, match="forbidden"):
            await repos.service().get_order_by_id(OTHER_USER_ID, ORDER_ID)


class TestCancelOrder:
    async def test_success(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, user_id=OWNER_ID, status=constant.OrderStatus.PAID)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)
        ctrl.expect(repos.orders.cancel_and_restock, ORDER_ID, constant.OrderStatus.PAID, returns=True)

        await repos.service().cancel_order(OWNER_ID, ORDER_ID)

    async def test_status_changed_concurrently(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, user_id=OWNER_ID, status=constant.OrderStatus.PROCESSING)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)
        ctrl.expect(repos.orders.cancel_and_restock, ORDER_ID, constant.OrderStatus.PROCESSING, returns=False)

        with pytest.raises(InvalidStatusError, match="status changed"):
            await repos.service().cancel_order(OWNER_ID, ORDER_ID)

    async def test_order_not_found(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="order not found"):
            await repos.service().cancel_order(OWNER_ID, ORDER_ID)

    async def test_forbidden_different_user(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, user_id=OWNER_ID, status=constant.OrderStatus.PENDING)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)

        with pytest.raises(ForbiddenError, match="forbidden"):
            await repos.service().cancel_order(OTHER_USER_ID, ORDER_ID)

    async def test_cannot_cancel_shipped_order(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, user_id=OWNER_ID, status=constant.OrderStatus.SHIPPED)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)

        with pytest.raises(InvalidStatusError, match="cannot cancel order with status"):
            await repos.service().cancel_order(OWNER_ID, ORDER_ID)


class TestUpdateOrderStatus:
    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (constant.OrderStatus.PAID, constant.OrderStatus.PROCESSING),
            (constant.OrderStatus.PROCESSING, constant.OrderStatus.SHIPPING),
        ],
        ids=["paid to processing", "processing to shipping"],
    )
    async def test_success(self, ctrl: Controller, current: str, new: str) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=Order(id=ORDER_ID, store_id=STORE_ID, status=current))
        ctrl.expect(repos.stores.find_by_user_id, SELLER_ID, returns=Store(id=STORE_ID))
        ctrl.expect(repos.orders.update_status_if_current, ORDER_ID, current, new, returns=True)

        await repos.service().update_order_status(SELLER_ID, ORDER_ID, new)

    async def test_status_changed_concurrently_e_g_buyer_cancelled(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, store_id=STORE_ID, status=constant.OrderStatus.PROCESSING)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)
        ctrl.expect(repos.stores.find_by_user_id, SELLER_ID, returns=Store(id=STORE_ID))
        ctrl.expect(
            repos.orders.update_status_if_current,
            ORDER_ID,
            constant.OrderStatus.PROCESSING,
            constant.OrderStatus.SHIPPING,
            returns=False,
        )

        with pytest.raises(InvalidStatusError, match="status changed"):
            await repos.service().update_order_status(SELLER_ID, ORDER_ID, constant.OrderStatus.SHIPPING)

    async def test_order_not_found(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="order not found"):
            await repos.service().update_order_status(SELLER_ID, ORDER_ID, constant.OrderStatus.PROCESSING)

    @pytest.mark.parametrize(
        ("current", "new", "message"),
        [
            (constant.OrderStatus.PENDING, constant.OrderStatus.PROCESSING, "cannot transition from status"),
            (constant.OrderStatus.PAID, constant.OrderStatus.SHIPPED, "invalid status transition"),
        ],
        ids=["pending has no valid transition", "paid cannot skip to shipped"],
    )
    async def test_invalid_transition(self, ctrl: Controller, current: str, new: str, message: str) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=Order(id=ORDER_ID, status=current))

        with pytest.raises(InvalidStatusError, match=message):
            await repos.service().update_order_status(SELLER_ID, ORDER_ID, new)

    async def test_store_not_found(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(
            id=ORDER_ID,
            status=constant.OrderStatus.PAID,
            order_items=[OrderItem(product_id=uuid.uuid4(), quantity=1)],
        )
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)
        ctrl.expect(repos.stores.find_by_user_id, SELLER_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="store not found"):
            await repos.service().update_order_status(SELLER_ID, ORDER_ID, constant.OrderStatus.PROCESSING)

    async def test_forbidden_order_not_from_the_sellers_store(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        order = Order(id=ORDER_ID, store_id=uuid.uuid4(), status=constant.OrderStatus.PAID)
        ctrl.expect(repos.orders.find_by_id, ORDER_ID, returns=order)
        ctrl.expect(repos.stores.find_by_user_id, SELLER_ID, returns=Store(id=STORE_ID))

        with pytest.raises(ForbiddenError, match="forbidden"):
            await repos.service().update_order_status(SELLER_ID, ORDER_ID, constant.OrderStatus.PROCESSING)


class TestGetSellerOrders:
    async def test_success(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.stores.find_by_user_id, USER_ID, returns=Store(id=STORE_ID, user_id=USER_ID))
        orders = [Order(id=uuid.uuid4(), user_id=uuid.uuid4(), status=constant.OrderStatus.PAID)]
        ctrl.expect(repos.orders.find_by_store_id, STORE_ID, 1, 10, returns=(orders, 1))

        resp, _ = await repos.service().get_seller_orders(USER_ID, 1, 10)

        assert len(resp) == 1

    async def test_store_not_found(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.stores.find_by_user_id, USER_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="store not found"):
            await repos.service().get_seller_orders(USER_ID, 1, 10)

    async def test_db_error_on_orders(self, ctrl: Controller) -> None:
        repos = Repos(ctrl)
        ctrl.expect(repos.stores.find_by_user_id, USER_ID, returns=Store(id=STORE_ID, user_id=USER_ID))
        ctrl.expect(repos.orders.find_by_store_id, STORE_ID, 1, 10, raises=db_error())

        with pytest.raises(InternalError):
            await repos.service().get_seller_orders(USER_ID, 1, 10)


class TestProcessPaymentResult:
    @pytest.mark.parametrize("success", [True, False], ids=["success", "failure"])
    @pytest.mark.parametrize("applied", [True, False], ids=["applied", "ignored for non-pending order"])
    async def test_result_is_applied_once(self, ctrl: Controller, success: bool, applied: bool) -> None:
        repos = Repos(ctrl)
        method = repos.orders.mark_payment_succeeded if success else repos.orders.mark_payment_failed
        ctrl.expect(method, ORDER_ID, returns=applied)

        await repos.service().process_payment_result(ORDER_ID, success)

    @pytest.mark.parametrize("success", [True, False], ids=["success", "failure"])
    async def test_database_error_is_raised_for_requeue(self, ctrl: Controller, success: bool) -> None:
        repos = Repos(ctrl)
        method = repos.orders.mark_payment_succeeded if success else repos.orders.mark_payment_failed
        ctrl.expect(method, ORDER_ID, raises=ConnectionError("db down"))

        with pytest.raises(ConnectionError):
            await repos.service().process_payment_result(ORDER_ID, success)
