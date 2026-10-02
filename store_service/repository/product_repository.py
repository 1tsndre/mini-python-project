import contextlib
import uuid
from typing import Any

from common import decimals, gojson
from store_service import constant, pagination
from store_service.model import Product, ProductFilter
from store_service.repository.caches import Cache
from store_service.repository.databases import Database
from store_service.repository.errors import RecordNotFoundError
from store_service.repository.rows import scan, scan_into

PRODUCT_COLUMNS = "id, store_id, category_id, name, description, price, stock, image_url, created_at, updated_at"

ALLOWED_PRODUCT_SORT_FIELDS = frozenset({"price", "name", "created_at"})


class ProductRepository:
    def __init__(self, db: Database, cache: Cache) -> None:
        self._db = db
        self._cache = cache

    async def create(self, product: Product) -> None:
        row = await self._db.fetchrow(
            """
		INSERT INTO products (store_id, category_id, name, description, price, stock, image_url)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		RETURNING """
            + PRODUCT_COLUMNS,
            product.store_id,
            product.category_id,
            product.name,
            product.description,
            product.price,
            product.stock,
            product.image_url,
        )
        scan_into(product, row)

    async def find_all(self, product_filter: ProductFilter) -> tuple[list[Product], int]:
        where, args = product_filter_query(product_filter)

        total = await self._db.fetchval("SELECT COUNT(*) FROM products" + where, *args)

        query = (
            f"SELECT {PRODUCT_COLUMNS} FROM products{where}{product_order_by(product_filter)}"
            f" LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}"
        )
        rows = await self._db.fetch(
            query, *args, product_filter.per_page, pagination.offset(product_filter.page, product_filter.per_page)
        )
        return [scan(Product, row) for row in rows], total

    async def find_by_id(self, product_id: uuid.UUID) -> Product:
        cache_key = constant.KEY_PRODUCT.format(product_id)

        with contextlib.suppress(Exception):
            return gojson.unmarshal(await self._cache.get(cache_key), Product)

        row = await self._db.fetchrow("SELECT " + PRODUCT_COLUMNS + " FROM products WHERE id = $1", product_id)
        if row is None:
            raise RecordNotFoundError()
        product = scan(Product, row)

        with contextlib.suppress(Exception):
            await self._cache.set(cache_key, product, constant.TTL_PRODUCT)

        return product

    async def update(self, product: Product) -> None:
        """Never writes stock: stock changes go through update_stock or the atomic checkout/cancel
        paths, otherwise a stale read here would overwrite a concurrent checkout's decrement. The
        stored row, including its current stock, is read back into product.
        """
        row = await self._db.fetchrow(
            """
		UPDATE products
		SET category_id = $1, name = $2, description = $3, price = $4, image_url = $5, updated_at = NOW()
		WHERE id = $6
		RETURNING """
            + PRODUCT_COLUMNS,
            product.category_id,
            product.name,
            product.description,
            product.price,
            product.image_url,
            product.id,
        )
        if row is None:
            raise RecordNotFoundError()
        scan_into(product, row)
        await self._invalidate(product.id)

    async def delete(self, product_id: uuid.UUID) -> None:
        await self._db.execute("DELETE FROM products WHERE id = $1", product_id)
        await self._invalidate(product_id)

    async def update_stock(self, product_id: uuid.UUID, quantity: int) -> None:
        await self._db.execute("UPDATE products SET stock = $1, updated_at = NOW() WHERE id = $2", quantity, product_id)
        await self._invalidate(product_id)

    async def _invalidate(self, product_id: uuid.UUID) -> None:
        with contextlib.suppress(Exception):
            await self._cache.delete(constant.KEY_PRODUCT.format(product_id))


def product_filter_query(product_filter: ProductFilter) -> tuple[str, list[Any]]:
    """The WHERE clause for the filter, with placeholders numbered from $1, and its arguments."""
    conds: list[str] = []
    args: list[Any] = []

    def arg(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if product_filter.category_id:
        conds.append("category_id = " + arg(product_filter.category_id))
    if product_filter.store_id:
        conds.append("store_id = " + arg(product_filter.store_id))
    if product_filter.search:
        search = arg("%" + product_filter.search + "%")
        conds.append("(name ILIKE " + search + " OR description ILIKE " + search + ")")
    if product_filter.min_price:
        min_price = decimals.parse(product_filter.min_price)
        if min_price is not None:
            conds.append("price >= " + arg(min_price))
    if product_filter.max_price:
        max_price = decimals.parse(product_filter.max_price)
        if max_price is not None:
            conds.append("price <= " + arg(max_price))

    if not conds:
        return "", []
    return " WHERE " + " AND ".join(conds), args


def product_order_by(product_filter: ProductFilter) -> str:
    """The ORDER BY clause. The column comes from ALLOWED_PRODUCT_SORT_FIELDS, so the clause is safe
    to put into the query.
    """
    sort_by = product_filter.sort_by if product_filter.sort_by in ALLOWED_PRODUCT_SORT_FIELDS else "created_at"
    sort_order = "ASC" if product_filter.sort_order == "asc" else "DESC"
    # id breaks ties, so products with equal values are neither repeated nor skipped across pages.
    return f" ORDER BY {sort_by} {sort_order}, id {sort_order}"
