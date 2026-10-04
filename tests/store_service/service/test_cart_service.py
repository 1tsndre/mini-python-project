import decimal
import uuid
from unittest.mock import ANY

import pytest

from store_service.model import AddCartItemRequest, Cart, CartItem, Product, UpdateCartItemRequest
from store_service.repository.cart_repository import CartRepository
from store_service.repository.product_repository import ProductRepository
from store_service.service.cart_service import CartService
from store_service.service.errors import InsufficientStockError, InternalError, NotFoundError, ValidationError
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, not_found

USER_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
OTHER_PRODUCT_ID = uuid.uuid4()


def new_service(ctrl: Controller) -> tuple[CartService, CartRepository, ProductRepository]:
    cart_repo = ctrl.mock(CartRepository)
    product_repo = ctrl.mock(ProductRepository)
    return CartService(cart_repo, product_repo, None), cart_repo, product_repo


def cart_with(quantity: int, **item: object) -> Cart:
    return Cart(user_id=USER_ID, items=[CartItem(product_id=PRODUCT_ID, quantity=quantity, **item)])


def product(stock: int, **fields: object) -> Product:
    return Product(id=PRODUCT_ID, stock=stock, **fields)


class TestGetCart:
    async def test_shows_the_current_product_price_not_the_stored_snapshot(self, ctrl: Controller) -> None:
        # The stored item is a snapshot from when it was added; checkout charges the current price,
        # so the cart must show the current price too.
        svc, cart_repo, product_repo = new_service(ctrl)
        stored = cart_with(2, name="Test Product", price=decimal.Decimal(10000))
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=stored)
        current = product(0, name="Test Product v2", price=decimal.Decimal(12500))
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=current)

        resp = await svc.get_cart(USER_ID)

        assert len(resp.items) == 1
        assert resp.items[0].name == "Test Product v2"
        assert resp.items[0].price == 12500
        assert resp.total == 25000

    async def test_product_cannot_be_loaded_stored_values_are_kept(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        stored = cart_with(2, name="Test Product", price=decimal.Decimal(10000))
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=stored)
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, raises=db_error())

        resp = await svc.get_cart(USER_ID)

        assert resp.items[0].name == "Test Product"
        assert resp.total == 20000

    async def test_repo_error(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, raises=ConnectionError("redis error"))

        with pytest.raises(InternalError):
            await svc.get_cart(USER_ID)


class TestAddItem:
    async def test_success_new_item(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        loaded = product(10, name="Test Product", price=decimal.Decimal(10000))
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=loaded)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=Cart(user_id=USER_ID, items=[]))
        ctrl.expect(cart_repo.save_cart, ANY)

        resp = await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=2))

        assert [(i.product_id, i.quantity) for i in resp.items] == [(PRODUCT_ID, 2)]
        assert resp.total == 20000

    async def test_load_cart_fails_cart_is_not_overwritten(self, ctrl: Controller) -> None:
        # Saving after a failed load would overwrite the user's whole cart.
        svc, cart_repo, product_repo = new_service(ctrl)
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(10))
        ctrl.expect(cart_repo.get_cart, USER_ID, raises=ConnectionResetError("connection reset"))

        with pytest.raises(InternalError, match="failed to load cart"):
            await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=1))

    async def test_success_existing_item_incremented(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        loaded = product(10, name="Test Product", price=decimal.Decimal(10000))
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=loaded)
        stored = cart_with(2, name="Test Product", price=decimal.Decimal(10000))
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=stored)
        ctrl.expect(cart_repo.save_cart, ANY)

        resp = await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=1))

        assert resp.items[0].quantity == 3

    async def test_insufficient_stock_existing_quantity_plus_new_exceeds_stock(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(3))
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(2))

        with pytest.raises(InsufficientStockError, match="insufficient stock"):
            await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=2))

    @pytest.mark.parametrize(
        ("req", "message"),
        [
            (AddCartItemRequest(product_id="not-a-uuid", quantity=1), "invalid product_id"),
            (AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=0), "quantity must be greater than 0"),
        ],
        ids=["invalid product_id", "quantity zero"],
    )
    async def test_invalid_request(self, ctrl: Controller, req: AddCartItemRequest, message: str) -> None:
        svc, _, _ = new_service(ctrl)

        with pytest.raises(ValidationError, match=message):
            await svc.add_item(USER_ID, req)

    async def test_product_not_found(self, ctrl: Controller) -> None:
        svc, _, product_repo = new_service(ctrl)
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="product not found"):
            await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=1))

    async def test_insufficient_stock(self, ctrl: Controller) -> None:
        svc, _, product_repo = new_service(ctrl)
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(3))

        with pytest.raises(InsufficientStockError, match="insufficient stock"):
            await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=5))

    async def test_save_cart_fails(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(10))
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=Cart(user_id=USER_ID, items=[]))
        ctrl.expect(cart_repo.save_cart, ANY, raises=ConnectionError("redis error"))

        with pytest.raises(InternalError, match="failed to save cart"):
            await svc.add_item(USER_ID, AddCartItemRequest(product_id=str(PRODUCT_ID), quantity=1))


class TestUpdateItem:
    async def test_success(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(10))
        ctrl.expect(cart_repo.save_cart, ANY)

        resp = await svc.update_item(USER_ID, PRODUCT_ID, UpdateCartItemRequest(quantity=3))

        assert resp.items[0].quantity == 3

    async def test_insufficient_stock(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(3))

        with pytest.raises(InsufficientStockError, match="insufficient stock"):
            await svc.update_item(USER_ID, PRODUCT_ID, UpdateCartItemRequest(quantity=5))

    async def test_quantity_zero(self, ctrl: Controller) -> None:
        svc, _, _ = new_service(ctrl)

        with pytest.raises(ValidationError, match="quantity must be greater than 0"):
            await svc.update_item(USER_ID, PRODUCT_ID, UpdateCartItemRequest(quantity=0))

    async def test_cart_not_loaded(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, raises=db_error())

        with pytest.raises(InternalError, match="failed to load cart"):
            await svc.update_item(USER_ID, PRODUCT_ID, UpdateCartItemRequest(quantity=1))

    async def test_item_not_in_cart(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))

        with pytest.raises(NotFoundError, match="item not found in cart"):
            await svc.update_item(USER_ID, OTHER_PRODUCT_ID, UpdateCartItemRequest(quantity=1))

    async def test_save_fails(self, ctrl: Controller) -> None:
        svc, cart_repo, product_repo = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=product(10))
        ctrl.expect(cart_repo.save_cart, ANY, raises=ConnectionError("redis error"))

        with pytest.raises(InternalError, match="failed to save cart"):
            await svc.update_item(USER_ID, PRODUCT_ID, UpdateCartItemRequest(quantity=2))


class TestRemoveItem:
    async def test_success(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))
        ctrl.expect(cart_repo.save_cart, ANY)

        resp = await svc.remove_item(USER_ID, PRODUCT_ID)

        assert resp.items == []

    async def test_cart_not_loaded(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, raises=db_error())

        with pytest.raises(InternalError, match="failed to load cart"):
            await svc.remove_item(USER_ID, PRODUCT_ID)

    async def test_item_not_in_cart(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))

        with pytest.raises(NotFoundError, match="item not found in cart"):
            await svc.remove_item(USER_ID, OTHER_PRODUCT_ID)

    async def test_save_fails(self, ctrl: Controller) -> None:
        svc, cart_repo, _ = new_service(ctrl)
        ctrl.expect(cart_repo.get_cart, USER_ID, returns=cart_with(1))
        ctrl.expect(cart_repo.save_cart, ANY, raises=ConnectionError("redis error"))

        with pytest.raises(InternalError, match="failed to save cart"):
            await svc.remove_item(USER_ID, PRODUCT_ID)
