"""The integration tests run the repositories against a real PostgreSQL when TEST_DATABASE_URL is set,
for example with the docker-compose database:

    TEST_DATABASE_URL="postgres://postgres:postgres@localhost:5432/mini_python_ecommerce?sslmode=disable" \\
        uv run pytest tests/store_service/repository/test_integration.py

They migrate a fresh schema and drop it afterwards, leaving the database's own data alone. Without the
variable they are skipped.
"""

import asyncio
import datetime as dt
import decimal
import os
import pathlib
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from common import gojson
from store_service import constant
from store_service.model import (
    Cart,
    CartItem,
    Category,
    Order,
    OrderItem,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Product,
    ProductFilter,
    Review,
    Store,
    User,
)
from store_service.model.zero import NIL_UUID, ZERO_TIME
from store_service.repository.cart_repository import CartRepository
from store_service.repository.category_repository import CategoryRepository
from store_service.repository.databases.postgres import PostgresDB
from store_service.repository.errors import DuplicateKeyError, ForeignKeyViolationError, RecordNotFoundError
from store_service.repository.order_repository import (
    OrderRepository,
    ProductNotFoundError,
    StockReservation,
    StockUnavailableError,
)
from store_service.repository.product_repository import ProductRepository
from store_service.repository.review_repository import ReviewRepository
from store_service.repository.store_repository import StoreRepository
from store_service.repository.user_repository import UserRepository

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
MIGRATIONS = pathlib.Path(__file__).parents[3] / "migrations"

pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"),
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db() -> AsyncIterator[PostgresDB]:
    """The test database: connections with search_path set to a new schema the up migrations ran in."""
    schema = f"repository_test_{time.time_ns()}"
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(f"CREATE SCHEMA {schema}")
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(
        TEST_DATABASE_URL, min_size=1, max_size=10, server_settings={"search_path": schema}
    )
    try:
        async with pool.acquire() as conn:
            for file in sorted(MIGRATIONS.glob("*.up.sql")):
                await conn.execute(file.read_text())
        yield PostgresDB(pool, log_queries=False)
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP SCHEMA {schema} CASCADE")
        await pool.close()


class MemCache:
    """An in-memory cache that stores JSON, like the Redis cache."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes:
        return self.data[key]

    async def set(self, key: str, value: Any, ttl: dt.timedelta) -> None:
        self.data[key] = gojson.marshal(value)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.data


def unique(prefix: str) -> str:
    return f"{prefix}-{str(uuid.uuid4())[:8]}"


class Fixtures:
    """The repositories under test, and the rows tests need. Every row gets unique values, so the tests
    can share one schema.
    """

    def __init__(self, db: PostgresDB) -> None:
        self.db = db
        self.cache = MemCache()
        self.users = UserRepository(db)
        self.stores = StoreRepository(db)
        self.categories = CategoryRepository(db)
        self.products = ProductRepository(db, self.cache)
        self.carts = CartRepository(db, self.cache)
        self.orders = OrderRepository(db, self.cache)
        self.reviews = ReviewRepository(db)

    async def user(self) -> User:
        user = User(
            email=unique("user") + "@example.com", password="hash", name=unique("User"), role=constant.Role.BUYER
        )
        await self.users.create(user)
        return user

    async def store(self) -> Store:
        store = Store(user_id=(await self.user()).id, name=unique("Store"))
        await self.stores.create(store)
        return store

    async def category(self) -> Category:
        category = Category(name=unique("Category"))
        await self.categories.create(category)
        return category

    async def product(self, store_id: uuid.UUID, category_id: uuid.UUID, name: str, price: str, stock: int) -> Product:
        product = Product(
            store_id=store_id, category_id=category_id, name=name, price=decimal.Decimal(price), stock=stock
        )
        await self.products.create(product)
        return product

    async def stock(self, product_id: uuid.UUID) -> int:
        """A product's stock read from the database, bypassing the cache."""
        return await self.db.fetchval("SELECT stock FROM products WHERE id = $1", product_id)

    async def place_order(self, buyer: User, product: Product, quantity: int) -> Order:
        """Checks out quantity of product for buyer as one pending order with a pending payment."""

        def build(reserved: list[Product]) -> list[Order]:
            total = reserved[0].price * quantity
            return [
                Order(
                    user_id=buyer.id,
                    store_id=reserved[0].store_id,
                    status=constant.OrderStatus.PENDING,
                    total_amount=total,
                    shipping_address="Jl. Test 1",
                    order_items=[OrderItem(product_id=reserved[0].id, quantity=quantity, price=reserved[0].price)],
                    payment=Payment(method=PaymentMethod.MOCK, status=PaymentStatus.PENDING, amount=total),
                )
            ]

        orders = await self.orders.create_orders_with_stock([StockReservation(product.id, quantity)], build)
        return orders[0]


@pytest.fixture
def f(db: PostgresDB) -> Fixtures:
    return Fixtures(db)


def ids(rows: list[Any]) -> list[uuid.UUID]:
    return [row.id for row in rows]


async def test_user_repository(f: Fixtures) -> None:
    user = await f.user()
    assert user.id != NIL_UUID
    assert user.created_at != ZERO_TIME

    found = await f.users.find_by_email(user.email.upper())
    assert found.id == user.id

    duplicate = User(email=user.email, password="hash", name="Duplicate", role=constant.Role.BUYER)
    with pytest.raises(DuplicateKeyError):
        await f.users.create(duplicate)

    await f.users.update_role(user.id, constant.Role.SELLER)
    assert (await f.users.find_by_id(user.id)).role == constant.Role.SELLER

    with pytest.raises(RecordNotFoundError):
        await f.users.find_by_id(uuid.uuid4())


async def test_store_repository(f: Fixtures) -> None:
    store = await f.store()

    with pytest.raises(DuplicateKeyError):
        await f.stores.create(Store(user_id=store.user_id, name="Second store"))

    store.name, store.description, store.logo_url = "Renamed", "New description", "/uploads/stores/logo.png"
    await f.stores.update(store)
    found = await f.stores.find_by_user_id(store.user_id)
    assert (found.name, found.description, found.logo_url) == ("Renamed", "New description", "/uploads/stores/logo.png")

    with pytest.raises(RecordNotFoundError):
        await f.stores.update(Store(id=uuid.uuid4(), name="Missing"))

    await f.stores.delete(store.id)
    with pytest.raises(RecordNotFoundError):
        await f.stores.find_by_id(store.id)


async def test_category_repository(f: Fixtures) -> None:
    first, second = Category(name=unique("A category")), Category(name=unique("B category"))
    await f.categories.create(first)
    await f.categories.create(second)

    with pytest.raises(DuplicateKeyError):
        await f.categories.create(Category(name=first.name))
    with pytest.raises(DuplicateKeyError):
        await f.categories.update(Category(id=second.id, name=first.name))

    position = {c.id: i for i, c in enumerate(await f.categories.find_all())}
    assert position[first.id] < position[second.id], "categories are sorted by name"

    store = await f.store()
    await f.product(store.id, first.id, unique("Product"), "10.00", 1)
    with pytest.raises(ForeignKeyViolationError):
        await f.categories.delete(first.id)

    await f.categories.delete(second.id)
    with pytest.raises(RecordNotFoundError):
        await f.categories.find_by_id(second.id)


class TestProductRepository:
    async def test_create_requires_an_existing_category(self, f: Fixtures) -> None:
        store = await f.store()
        product = Product(store_id=store.id, category_id=uuid.uuid4(), name="Orphan", price=decimal.Decimal(1))

        with pytest.raises(ForeignKeyViolationError):
            await f.products.create(product)

    async def test_update_never_writes_stock(self, f: Fixtures) -> None:
        store, category = await f.store(), await f.category()
        product = await f.product(store.id, category.id, unique("Mug"), "100.50", 5)
        await f.products.update_stock(product.id, 7)

        stale = Product(**vars(product))  # still holds stock 5
        stale.name, stale.price = "Renamed mug", decimal.Decimal("99.99")
        await f.products.update(stale)
        assert stale.stock == 7, "update reads back the stored stock"
        assert await f.stock(product.id) == 7

        found = await f.products.find_by_id(product.id)
        assert found.name == "Renamed mug"
        assert found.price == decimal.Decimal("99.99")

    async def test_find_all_filters_sorts_and_pages(self, f: Fixtures) -> None:
        shop, category, other = await f.store(), await f.category(), await f.category()
        token = unique("findall")
        cheap = await f.product(shop.id, category.id, token + " cheap", "5.00", 1)
        mid = await f.product(shop.id, category.id, token + " mid", "50.00", 1)
        pricey = await f.product(shop.id, other.id, token + " pricey", "500.00", 1)

        products, total = await f.products.find_all(
            ProductFilter(
                store_id=str(shop.id), search=token.upper(), min_price="10",
                sort_by="price", sort_order="asc", page=1, per_page=10,
            )
        )  # fmt: skip
        assert total == 2
        assert ids(products) == [mid.id, pricey.id]

        products, total = await f.products.find_all(
            ProductFilter(store_id=str(shop.id), category_id=str(other.id), page=1, per_page=10)
        )
        assert total == 1
        assert ids(products) == [pricey.id]

        products, total = await f.products.find_all(
            ProductFilter(store_id=str(shop.id), sort_by="price", sort_order="desc", page=2, per_page=2)
        )
        assert total == 3, "the total ignores pagination"
        assert ids(products) == [cheap.id]

    async def test_delete_refuses_products_that_orders_reference(self, f: Fixtures) -> None:
        store, category = await f.store(), await f.category()
        ordered = await f.product(store.id, category.id, unique("Lamp"), "20.00", 3)
        await f.place_order(await f.user(), ordered, 1)
        with pytest.raises(ForeignKeyViolationError):
            await f.products.delete(ordered.id)

        unused = await f.product(store.id, category.id, unique("Vase"), "20.00", 3)
        await f.products.delete(unused.id)
        with pytest.raises(RecordNotFoundError):
            await f.products.find_by_id(unused.id)


async def test_cart_repository(f: Fixtures) -> None:
    buyer, store, category = await f.user(), await f.store(), await f.category()
    first = await f.product(store.id, category.id, unique("Pen"), "10.00", 5)
    second = await f.product(store.id, category.id, unique("Book"), "20.00", 5)

    cart = Cart(
        user_id=buyer.id,
        items=[CartItem(product_id=first.id, quantity=2), CartItem(product_id=second.id, quantity=1)],
    )
    await f.carts.save_cart(cart)

    # Without the cached copy, get_cart reads the PostgreSQL backup.
    f.cache.data.clear()
    loaded = await f.carts.get_cart(buyer.id)
    items = {item.product_id: item for item in loaded.items}
    assert len(items) == 2
    assert items[first.id].quantity == 2
    assert items[first.id].name == first.name
    assert items[first.id].price == first.price
    assert items[second.id].quantity == 1

    cart.items = cart.items[:1]
    await f.carts.save_cart(cart)
    f.cache.data.clear()
    assert len((await f.carts.get_cart(buyer.id)).items) == 1, "saving replaces the stored items"

    await f.carts.delete_cart(buyer.id)
    assert (await f.carts.get_cart(buyer.id)).items == [], "an empty cart has an empty list"


class TestOrderRepository:
    async def test_checkout_reserves_stock_and_stores_the_order(self, f: Fixtures) -> None:
        buyer, store, category = await f.user(), await f.store(), await f.category()
        product = await f.product(store.id, category.id, unique("Mug"), "25.00", 5)

        order = await f.place_order(buyer, product, 2)
        assert order.id != NIL_UUID
        assert len(order.order_items) == 1
        assert order.order_items[0].id != NIL_UUID
        assert order.payment is not None
        assert order.payment.id != NIL_UUID
        assert await f.stock(product.id) == 3

        found = await f.orders.find_by_id(order.id)
        assert found.status == constant.OrderStatus.PENDING
        assert found.total_amount == decimal.Decimal("50.00")
        assert [i.quantity for i in found.order_items] == [2]
        assert found.payment is not None
        assert found.payment.status == PaymentStatus.PENDING
        assert found.payment.paid_at is None

    async def test_a_failed_reservation_writes_nothing(self, f: Fixtures) -> None:
        store, category = await f.store(), await f.category()
        scarce = await f.product(store.id, category.id, unique("Scarce"), "10.00", 1)
        plenty = await f.product(store.id, category.id, unique("Plenty"), "10.00", 5)

        def build(reserved: list[Product]) -> list[Order]:
            raise AssertionError("build must not run when a reservation fails")

        with pytest.raises(StockUnavailableError) as insufficient:
            await f.orders.create_orders_with_stock(
                [StockReservation(plenty.id, 1), StockReservation(scarce.id, 2)], build
            )
        assert insufficient.value.product_name == scarce.name
        assert await f.stock(plenty.id) == 5, "the earlier reservation is rolled back"
        assert await f.stock(scarce.id) == 1

        with pytest.raises(ProductNotFoundError):
            await f.orders.create_orders_with_stock([StockReservation(uuid.uuid4(), 1)], build)

    async def test_concurrent_checkouts_cannot_oversell(self, f: Fixtures) -> None:
        store, category = await f.store(), await f.category()
        product = await f.product(store.id, category.id, unique("Last one"), "10.00", 1)
        buyers = [await f.user() for _ in range(3)]

        results = await asyncio.gather(*(f.place_order(b, product, 1) for b in buyers), return_exceptions=True)

        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(results) - len(failures) == 1
        assert all(isinstance(e, StockUnavailableError) for e in failures)
        assert await f.stock(product.id) == 0

    async def test_lists_by_buyer_and_by_store_newest_first_with_details(self, f: Fixtures) -> None:
        shopper, shop, category = await f.user(), await f.store(), await f.category()
        product = await f.product(shop.id, category.id, unique("Lamp"), "15.00", 10)
        older = await f.place_order(shopper, product, 1)
        newer = await f.place_order(shopper, product, 2)

        orders, total = await f.orders.find_by_user_id(shopper.id, 1, 10)
        assert total == 2
        assert ids(orders) == [newer.id, older.id]
        for order in orders:
            assert len(order.order_items) == 1
            assert order.payment is not None

        page, total = await f.orders.find_by_store_id(shop.id, 2, 1)
        assert total == 2
        assert ids(page) == [older.id]

    async def test_status_updates_are_compare_and_set(self, f: Fixtures) -> None:
        buyer, store, category = await f.user(), await f.store(), await f.category()
        product = await f.product(store.id, category.id, unique("Pencil"), "3.00", 10)
        order = await f.place_order(buyer, product, 1)

        updated = await f.orders.update_status_if_current(
            order.id, constant.OrderStatus.PAID, constant.OrderStatus.PROCESSING
        )
        assert not updated, "the order is still pending"

        assert await f.orders.mark_payment_succeeded(order.id)
        assert not await f.orders.mark_payment_succeeded(order.id), "a duplicate payment result changes nothing"

        found = await f.orders.find_by_id(order.id)
        assert found.status == constant.OrderStatus.PAID
        assert found.payment is not None
        assert found.payment.status == PaymentStatus.SUCCESS
        assert found.payment.paid_at is not None

        assert await f.orders.update_status_if_current(
            order.id, constant.OrderStatus.PAID, constant.OrderStatus.PROCESSING
        )

    async def test_cancellation_and_failed_payments_restock_once(self, f: Fixtures) -> None:
        buyer, store, category = await f.user(), await f.store(), await f.category()
        product = await f.product(store.id, category.id, unique("Plate"), "8.00", 10)
        cancelled = await f.place_order(buyer, product, 3)
        failed = await f.place_order(buyer, product, 2)
        assert await f.stock(product.id) == 5

        assert await f.orders.cancel_and_restock(cancelled.id, constant.OrderStatus.PENDING)
        assert not await f.orders.cancel_and_restock(cancelled.id, constant.OrderStatus.PENDING), (
            "cancelling again changes nothing"
        )
        assert await f.stock(product.id) == 8

        assert await f.orders.mark_payment_failed(failed.id)
        assert await f.stock(product.id) == 10

        for order_id, payment_status in [(cancelled.id, PaymentStatus.CANCELLED), (failed.id, PaymentStatus.FAILED)]:
            found = await f.orders.find_by_id(order_id)
            assert found.status == constant.OrderStatus.CANCELLED
            assert found.payment is not None
            assert found.payment.status == payment_status

    async def test_stale_pending_orders(self, f: Fixtures) -> None:
        buyer, store, category = await f.user(), await f.store(), await f.category()
        product = await f.product(store.id, category.id, unique("Cup"), "1.00", 10)
        order = await f.place_order(buyer, product, 1)
        now = dt.datetime.now().astimezone()

        assert order.id in ids(await f.orders.find_stale_pending(now + dt.timedelta(minutes=1), 1000))
        assert order.id not in ids(await f.orders.find_stale_pending(now - dt.timedelta(hours=1), 1000))


async def test_review_repository(f: Fixtures) -> None:
    buyer, store = await f.user(), await f.store()
    product = await f.product(store.id, (await f.category()).id, unique("Chair"), "12.00", 5)

    assert not await f.reviews.has_user_purchased(buyer.id, product.id)

    order = await f.place_order(buyer, product, 1)
    assert not await f.reviews.has_user_purchased(buyer.id, product.id), "a pending order is not a purchase"

    assert await f.orders.update_status_if_current(order.id, constant.OrderStatus.PENDING, constant.OrderStatus.SHIPPED)
    assert await f.reviews.has_user_purchased(buyer.id, product.id)

    review = Review(user_id=buyer.id, product_id=product.id, rating=4, comment="Solid")
    await f.reviews.create(review)
    assert review.id != NIL_UUID

    with pytest.raises(DuplicateKeyError):
        await f.reviews.create(Review(user_id=buyer.id, product_id=product.id, rating=1))

    assert await f.reviews.has_user_reviewed(buyer.id, product.id)

    reviews, total = await f.reviews.find_by_product_id(product.id, 1, 10)
    assert total == 1
    assert [(r.user_name, r.comment) for r in reviews] == [(buyer.name, "Solid")]
