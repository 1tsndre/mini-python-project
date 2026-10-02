import contextlib
import uuid

from common import gojson
from store_service import constant
from store_service.model import Cart, CartItem, CartItemDB
from store_service.repository.caches import Cache
from store_service.repository.databases import Database
from store_service.repository.rows import scan


class CartRepository:
    """Carts live in Redis, with the cart_items table as the durable backup."""

    def __init__(self, db: Database, cache: Cache) -> None:
        self._db = db
        self._cache = cache

    async def get_cart(self, user_id: uuid.UUID) -> Cart:
        """The user's cart; a user without one gets an empty cart."""
        cache_key = constant.KEY_CART.format(user_id)

        with contextlib.suppress(Exception):
            return gojson.unmarshal(await self._cache.get(cache_key), Cart)

        rows = await self._db.fetch(
            """
		SELECT ci.product_id, ci.quantity, p.name, p.price, p.image_url
		FROM cart_items ci
		JOIN products p ON p.id = ci.product_id
		WHERE ci.user_id = $1""",
            user_id,
        )
        cart = Cart(user_id=user_id, items=[scan(CartItem, row) for row in rows])

        with contextlib.suppress(Exception):
            await self._cache.set(cache_key, cart, constant.TTL_CART)

        return cart

    async def save_cart(self, cart: Cart) -> None:
        async with self._db.transaction() as tx:
            await tx.execute("DELETE FROM cart_items WHERE user_id = $1", cart.user_id)

            if cart.items:
                db_items = [
                    CartItemDB(user_id=cart.user_id, product_id=item.product_id, quantity=item.quantity)
                    for item in cart.items
                ]
                values = ", ".join(f"(${3 * i + 1}, ${3 * i + 2}, ${3 * i + 3})" for i in range(len(db_items)))
                args = [value for item in db_items for value in (item.user_id, item.product_id, item.quantity)]
                await tx.execute("INSERT INTO cart_items (user_id, product_id, quantity) VALUES " + values, *args)

        with contextlib.suppress(Exception):
            await self._cache.set(constant.KEY_CART.format(cart.user_id), cart, constant.TTL_CART)

    async def delete_cart(self, user_id: uuid.UUID) -> None:
        await self._db.execute("DELETE FROM cart_items WHERE user_id = $1", user_id)
        with contextlib.suppress(Exception):
            await self._cache.delete(constant.KEY_CART.format(user_id))
