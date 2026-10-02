import datetime as dt
import decimal
import uuid
from dataclasses import dataclass, field

from store_service.model.zero import NIL_UUID, ZERO_DECIMAL, ZERO_TIME


@dataclass
class CartItem:
    product_id: uuid.UUID = NIL_UUID
    name: str = ""
    price: decimal.Decimal = ZERO_DECIMAL
    quantity: int = 0
    image_url: str = ""


@dataclass
class Cart:
    user_id: uuid.UUID = NIL_UUID
    items: list[CartItem] = field(default_factory=list)
    updated_at: dt.datetime = ZERO_TIME


@dataclass
class CartItemDB:
    """The PostgreSQL backup of a cart line (table cart_items)."""

    id: uuid.UUID = NIL_UUID
    user_id: uuid.UUID = NIL_UUID
    product_id: uuid.UUID = NIL_UUID
    quantity: int = 0
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME


@dataclass
class AddCartItemRequest:
    product_id: str = ""
    quantity: int = 0


@dataclass
class UpdateCartItemRequest:
    quantity: int = 0


@dataclass
class CartItemResponse:
    product_id: uuid.UUID
    name: str
    price: decimal.Decimal
    quantity: int
    subtotal: decimal.Decimal
    image_url: str


@dataclass
class CartResponse:
    items: list[CartItemResponse]
    total: decimal.Decimal
    updated_at: dt.datetime
