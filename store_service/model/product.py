import datetime as dt
import decimal
import uuid
from dataclasses import dataclass

from store_service.model.zero import NIL_UUID, ZERO_DECIMAL, ZERO_TIME


@dataclass
class Product:
    id: uuid.UUID = NIL_UUID
    store_id: uuid.UUID = NIL_UUID
    category_id: uuid.UUID = NIL_UUID
    name: str = ""
    description: str = ""
    price: decimal.Decimal = ZERO_DECIMAL
    stock: int = 0
    image_url: str = ""
    created_at: dt.datetime = ZERO_TIME
    updated_at: dt.datetime = ZERO_TIME

    def to_response(self) -> "ProductResponse":
        return ProductResponse(
            id=self.id,
            store_id=self.store_id,
            category_id=self.category_id,
            name=self.name,
            description=self.description,
            price=self.price,
            stock=self.stock,
            image_url=self.image_url,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class CreateProductRequest:
    category_id: str = ""
    name: str = ""
    description: str = ""
    price: str = ""
    stock: int = 0


@dataclass
class UpdateProductRequest:
    category_id: str = ""
    name: str = ""
    description: str = ""
    price: str = ""
    stock: int | None = None


@dataclass
class ProductFilter:
    category_id: str = ""
    store_id: str = ""
    search: str = ""
    min_price: str = ""
    max_price: str = ""
    sort_by: str = ""
    sort_order: str = ""
    page: int = 0
    per_page: int = 0


@dataclass
class ProductResponse:
    id: uuid.UUID
    store_id: uuid.UUID
    category_id: uuid.UUID
    name: str
    description: str
    price: decimal.Decimal
    stock: int
    image_url: str
    created_at: dt.datetime
    updated_at: dt.datetime
