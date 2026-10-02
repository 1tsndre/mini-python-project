import decimal
import uuid

import structlog

from common import decimals
from store_service import pagination
from store_service.model import (
    CreateProductRequest,
    Product,
    ProductFilter,
    ProductResponse,
    Store,
    UpdateProductRequest,
)
from store_service.repository.errors import ForeignKeyViolationError
from store_service.repository.product_repository import ProductRepository
from store_service.repository.store_repository import StoreRepository
from store_service.service.errors import ConflictError, ForbiddenError, InternalError, NotFoundError, ValidationError
from store_service.util import uuids

log = structlog.get_logger()

# The smallest price that no longer fits the DECIMAL(15,2) columns.
MAX_PRICE = decimal.Decimal(10) ** 13
_INT32_MAX = 2**31 - 1


def validate_price(price: decimal.Decimal) -> None:
    """Rejects prices PostgreSQL would refuse or silently round, so the stored price is always
    exactly the one the seller sent.
    """
    if price <= 0:
        raise ValidationError("price must be greater than 0")
    if price >= MAX_PRICE:
        raise ValidationError("price is too large")
    if price != price.quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP, context=_EXACT):
        raise ValidationError("price must have at most 2 decimal places")


# Enough precision for any price below MAX_PRICE, so quantize never rounds the integer part.
_EXACT = decimal.Context(prec=100)


def validate_stock(stock: int) -> None:
    """Rejects stock values the INTEGER column cannot hold."""
    if stock < 0:
        raise ValidationError("stock must not be negative")
    if stock > _INT32_MAX:
        raise ValidationError("stock is too large")


def _parse_price(text: str) -> decimal.Decimal:
    price = decimals.parse(text)
    if price is None:
        raise ValidationError("invalid price")
    validate_price(price)
    return price


def _parse_category_id(text: str) -> uuid.UUID:
    category_id = uuids.parse(text)
    if category_id is None:
        raise ValidationError("invalid category_id")
    return category_id


class ProductService:
    def __init__(self, product_repo: ProductRepository, store_repo: StoreRepository) -> None:
        self._product_repo = product_repo
        self._store_repo = store_repo

    async def _get_store_by_owner(self, user_id: uuid.UUID) -> Store:
        try:
            return await self._store_repo.find_by_user_id(user_id)
        except Exception as e:
            raise NotFoundError("store not found for this user") from e

    async def _find_product(self, product_id: uuid.UUID) -> Product:
        try:
            return await self._product_repo.find_by_id(product_id)
        except Exception as e:
            raise NotFoundError("product not found") from e

    async def create_product(self, user_id: uuid.UUID, req: CreateProductRequest) -> ProductResponse:
        store = await self._get_store_by_owner(user_id)
        category_id = _parse_category_id(req.category_id)
        price = _parse_price(req.price)
        validate_stock(req.stock)

        product = Product(
            store_id=store.id,
            category_id=category_id,
            name=req.name,
            description=req.description,
            price=price,
            stock=req.stock,
        )

        try:
            await self._product_repo.create(product)
        except ForeignKeyViolationError as e:
            raise NotFoundError("category not found") from e
        except Exception as e:
            log.error("failed to create product", error=str(e))
            raise InternalError("failed to create product") from e

        log.info("product created", product_id=str(product.id), store_id=str(store.id))

        return product.to_response()

    async def get_products(self, product_filter: ProductFilter) -> tuple[list[ProductResponse], int]:
        product_filter.page, product_filter.per_page = pagination.normalize(
            product_filter.page, product_filter.per_page
        )

        try:
            products, total = await self._product_repo.find_all(product_filter)
        except Exception as e:
            log.error("failed to fetch products", error=str(e))
            raise InternalError("failed to fetch products") from e

        return [p.to_response() for p in products], total

    async def get_product_by_id(self, product_id: uuid.UUID) -> ProductResponse:
        return (await self._find_product(product_id)).to_response()

    async def update_product(
        self, user_id: uuid.UUID, product_id: uuid.UUID, req: UpdateProductRequest
    ) -> ProductResponse:
        store = await self._get_store_by_owner(user_id)
        product = await self._find_product(product_id)

        if product.store_id != store.id:
            raise ForbiddenError("forbidden: not product owner")

        if req.name:
            product.name = req.name
        if req.description:
            product.description = req.description
        if req.price:
            product.price = _parse_price(req.price)
        if req.category_id:
            product.category_id = _parse_category_id(req.category_id)
        if req.stock is not None:
            validate_stock(req.stock)

        try:
            await self._product_repo.update(product)
        except ForeignKeyViolationError as e:
            raise NotFoundError("category not found") from e
        except Exception as e:
            log.error("failed to update product", error=str(e))
            raise InternalError("failed to update product") from e

        # Stock is written on its own, never through update, so a stale product read above cannot
        # overwrite the atomic decrements made by concurrent checkouts.
        if req.stock is not None:
            try:
                await self._product_repo.update_stock(product_id, req.stock)
            except Exception as e:
                log.error("failed to update product stock", error=str(e))
                raise InternalError("failed to update product stock") from e
            product.stock = req.stock

        return product.to_response()

    async def delete_product(self, user_id: uuid.UUID, product_id: uuid.UUID) -> None:
        store = await self._get_store_by_owner(user_id)
        product = await self._find_product(product_id)

        if product.store_id != store.id:
            raise ForbiddenError("forbidden: not product owner")

        try:
            await self._product_repo.delete(product_id)
        except ForeignKeyViolationError as e:
            raise ConflictError("product is in use by existing orders or carts") from e
        except Exception as e:
            log.error("failed to delete product", error=str(e))
            raise InternalError("failed to delete product") from e

    async def update_image(self, user_id: uuid.UUID, product_id: uuid.UUID, image_url: str) -> ProductResponse:
        store = await self._get_store_by_owner(user_id)
        product = await self._find_product(product_id)

        if product.store_id != store.id:
            raise ForbiddenError("forbidden: not product owner")

        product.image_url = image_url
        try:
            await self._product_repo.update(product)
        except Exception as e:
            log.error("failed to update product image", error=str(e))
            raise InternalError("failed to update product image") from e

        return product.to_response()
