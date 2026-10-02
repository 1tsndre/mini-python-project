import contextlib
import datetime as dt
import decimal
import uuid
from collections.abc import AsyncIterator

from store_service.model import (
    AddCartItemRequest,
    Cart,
    CartItem,
    CartItemResponse,
    CartResponse,
    Product,
    UpdateCartItemRequest,
)
from store_service.repository.cart_repository import CartRepository
from store_service.repository.product_repository import ProductRepository
from store_service.service.cart_lock import CartLock
from store_service.service.errors import InsufficientStockError, InternalError, NotFoundError, ValidationError
from store_service.util import uuids

INSUFFICIENT_STOCK = "insufficient stock"


@contextlib.asynccontextmanager
async def lock_user_cart(lock: CartLock | None, user_id: uuid.UUID) -> AsyncIterator[None]:
    """Takes the per-user cart lock shared by cart updates and checkout. A None lock (unit tests)
    skips locking, like the Go service's nil redsync.
    """
    if lock is None:
        yield
        return
    async with lock.hold(user_id):
        yield


class CartService:
    def __init__(self, cart_repo: CartRepository, product_repo: ProductRepository, lock: CartLock | None) -> None:
        self._cart_repo = cart_repo
        self._product_repo = product_repo
        self._lock = lock

    async def get_cart(self, user_id: uuid.UUID) -> CartResponse:
        try:
            cart = await self._cart_repo.get_cart(user_id)
        except Exception as e:
            raise InternalError("failed to fetch cart") from e
        await self._refresh_items(cart)
        return to_cart_response(cart)

    async def _refresh_items(self, cart: Cart, *loaded: Product) -> None:
        """Sets each item's name, price and image to the product's current values. The stored values
        are a snapshot from when the item was added, while checkout charges the current price, so
        showing the snapshot could show a total the buyer will not actually pay. Products the caller
        already loaded are reused; if a product cannot be loaded, its stored values are kept.
        """
        known = {p.id: p for p in loaded}
        for item in cart.items:
            product = known.get(item.product_id)
            if product is None:
                try:
                    product = await self._product_repo.find_by_id(item.product_id)
                except Exception:  # noqa: BLE001, S112
                    continue
            item.name = product.name
            item.price = product.price
            item.image_url = product.image_url

    async def add_item(self, user_id: uuid.UUID, req: AddCartItemRequest) -> CartResponse:
        product_id = uuids.parse(req.product_id)
        if product_id is None:
            raise ValidationError("invalid product_id")

        if req.quantity <= 0:
            raise ValidationError("quantity must be greater than 0")

        product = await self._find_product(product_id)
        if product.stock < req.quantity:
            raise InsufficientStockError(INSUFFICIENT_STOCK)

        async with lock_user_cart(self._lock, user_id):
            # get_cart returns an empty cart when the user has none, so an error here is a real
            # failure. Saving over it would replace the whole cart with this one item.
            cart = await self._load_cart(user_id)

            for item in cart.items:
                if item.product_id == product_id:
                    if product.stock < item.quantity + req.quantity:
                        raise InsufficientStockError(INSUFFICIENT_STOCK)
                    item.quantity += req.quantity
                    break
            else:
                cart.items.append(
                    CartItem(
                        product_id=product_id,
                        name=product.name,
                        price=product.price,
                        quantity=req.quantity,
                        image_url=product.image_url,
                    )
                )

            await self._refresh_items(cart, product)
            return await self._save(cart)

    async def update_item(self, user_id: uuid.UUID, product_id: uuid.UUID, req: UpdateCartItemRequest) -> CartResponse:
        if req.quantity <= 0:
            raise ValidationError("quantity must be greater than 0")

        async with lock_user_cart(self._lock, user_id):
            cart = await self._load_cart(user_id)

            item = next((i for i in cart.items if i.product_id == product_id), None)
            if item is None:
                raise NotFoundError("item not found in cart")

            product = await self._find_product(product_id)
            if product.stock < req.quantity:
                raise InsufficientStockError(INSUFFICIENT_STOCK)
            item.quantity = req.quantity

            await self._refresh_items(cart, product)
            return await self._save(cart)

    async def remove_item(self, user_id: uuid.UUID, product_id: uuid.UUID) -> CartResponse:
        async with lock_user_cart(self._lock, user_id):
            cart = await self._load_cart(user_id)

            index = next((i for i, item in enumerate(cart.items) if item.product_id == product_id), -1)
            if index == -1:
                raise NotFoundError("item not found in cart")
            del cart.items[index]

            await self._refresh_items(cart)
            return await self._save(cart)

    async def _load_cart(self, user_id: uuid.UUID) -> Cart:
        try:
            return await self._cart_repo.get_cart(user_id)
        except Exception as e:
            raise InternalError("failed to load cart") from e

    async def _save(self, cart: Cart) -> CartResponse:
        cart.updated_at = dt.datetime.now().astimezone()
        try:
            await self._cart_repo.save_cart(cart)
        except Exception as e:
            raise InternalError("failed to save cart") from e
        return to_cart_response(cart)

    async def _find_product(self, product_id: uuid.UUID) -> Product:
        try:
            return await self._product_repo.find_by_id(product_id)
        except Exception as e:
            raise NotFoundError("product not found") from e


def to_cart_response(cart: Cart) -> CartResponse:
    total = decimal.Decimal(0)
    items = []
    for item in cart.items:
        subtotal = item.price * item.quantity
        total += subtotal
        items.append(
            CartItemResponse(
                product_id=item.product_id,
                name=item.name,
                price=item.price,
                quantity=item.quantity,
                subtotal=subtotal,
                image_url=item.image_url,
            )
        )
    return CartResponse(items=items, total=total, updated_at=cart.updated_at)
