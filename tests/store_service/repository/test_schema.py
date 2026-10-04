"""The SQL in the repositories is written by hand. These tests check the column lists and the models'
fields against the schema in migrations/, so a misspelled column fails here instead of at runtime,
without needing a database.
"""

import dataclasses
import pathlib
import re

import pytest

from store_service import constant
from store_service.model import (
    CartItem,
    CartItemDB,
    Category,
    Order,
    OrderItem,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Product,
    Review,
    Store,
    User,
)
from store_service.repository.category_repository import CATEGORY_COLUMNS
from store_service.repository.order_repository import ORDER_COLUMNS, ORDER_ITEM_COLUMNS, PAYMENT_COLUMNS
from store_service.repository.product_repository import PRODUCT_COLUMNS
from store_service.repository.review_repository import REVIEW_COLUMNS
from store_service.repository.rows import scan, scan_into
from store_service.repository.store_repository import STORE_COLUMNS
from store_service.repository.user_repository import USER_COLUMNS

MIGRATIONS = pathlib.Path(__file__).parents[3] / "migrations"
CREATE_TABLE_RE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.DOTALL)
ADD_COLUMN_RE = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)")
TABLE_CONSTRAINTS = ("primary", "foreign", "check", "constraint")


def migrated_schema() -> dict[str, set[str]]:
    files = sorted(MIGRATIONS.glob("*.up.sql"))
    assert files
    schema: dict[str, set[str]] = {}
    for file in files:
        sql = file.read_text()
        for table, body in CREATE_TABLE_RE.findall(sql):
            columns = set()
            for line in body.splitlines():
                fields = line.split()
                if not fields:
                    continue
                name = fields[0].lower()
                # Table constraints, e.g. UNIQUE(user_id, product_id), are not columns.
                if name.startswith("unique") or name in TABLE_CONSTRAINTS:
                    continue
                columns.add(name)
            schema[table] = columns
        for table, column in ADD_COLUMN_RE.findall(sql):
            assert table in schema, f"{file.name} alters a table no migration creates"
            schema[table].add(column)
    return schema


SCHEMA = migrated_schema()


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("users", USER_COLUMNS),
        ("stores", STORE_COLUMNS),
        ("categories", CATEGORY_COLUMNS),
        ("products", PRODUCT_COLUMNS),
        ("reviews", REVIEW_COLUMNS),
        ("orders", ORDER_COLUMNS),
        ("order_items", ORDER_ITEM_COLUMNS),
        ("payments", PAYMENT_COLUMNS),
    ],
)
def test_column_lists_match_schema(table: str, columns: str) -> None:
    assert table in SCHEMA, f"table {table} is not in the migrations"
    for column in columns.split(", "):
        assert column in SCHEMA[table], f"column {table}.{column} does not exist"


# Fields that are not columns of the model's table: loaded from other tables, or computed by a query.
NOT_STORED = {"order_items", "payment", "user_name"}


@pytest.mark.parametrize(
    ("model", "tables"),
    [
        (User, ["users"]),
        (Store, ["stores"]),
        (Category, ["categories"]),
        (Product, ["products"]),
        (Review, ["reviews"]),
        (Order, ["orders"]),
        (OrderItem, ["order_items"]),
        (Payment, ["payments"]),
        (CartItemDB, ["cart_items"]),
        # The cart is read by joining cart_items with products.
        (CartItem, ["cart_items", "products"]),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else None,
)
def test_model_fields_match_schema(model: type, tables: list[str]) -> None:
    for field in dataclasses.fields(model):
        if field.name in NOT_STORED:
            continue
        assert any(field.name in SCHEMA[t] for t in tables), (
            f"{model.__name__}.{field.name} is not a column of {tables}"
        )


def test_enum_columns_are_scanned_as_their_enum() -> None:
    assert scan(User, {"role": "seller"}).role is constant.Role.SELLER

    order = Order()
    scan_into(order, {"status": "paid"})
    assert order.status is constant.OrderStatus.PAID

    payment = scan(Payment, {"method": "mock", "status": "cancelled"})
    assert payment.method is PaymentMethod.MOCK
    assert payment.status is PaymentStatus.CANCELLED
