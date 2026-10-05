import uuid
from unittest.mock import ANY

import pytest

from store_service import constant
from store_service.handler.cart_handler import CartHandler
from store_service.handler.order_handler import OrderHandler
from store_service.model import Cart, CartItem, Order, Product
from store_service.repository.cart_repository import CartRepository
from store_service.repository.order_repository import OrderRepository, StockUnavailableError
from store_service.repository.product_repository import ProductRepository
from store_service.repository.store_repository import StoreRepository
from store_service.service.cart_service import CartService
from store_service.service.order_service import OrderService
from tests.mocks import Controller
from tests.store_service.handler.helpers import call, decode_error, make_request


def order_service(
    ctrl: Controller, order_repo: OrderRepository, cart_repo: CartRepository | None = None
) -> OrderService:
    return OrderService(order_repo, cart_repo or ctrl.mock(CartRepository), ctrl.mock(StoreRepository), None, None)


# A seller-supplied status is echoed in the error message; words like "failed" in it must not turn a
# plain invalid request into a 500.
@pytest.mark.parametrize("status", ["failed", "not found", "forbidden", "unknown"])
async def test_update_order_status_invalid_status_is_400_whatever_it_says(ctrl: Controller, status: str) -> None:
    order_id = uuid.uuid4()
    order_repo = ctrl.mock(OrderRepository)
    ctrl.expect(order_repo.find_by_id, order_id, returns=Order(id=order_id, status=constant.OrderStatus.PAID))
    handler = OrderHandler(order_service(ctrl, order_repo), None)

    request = make_request(
        "PUT",
        f"/api/v1/orders/{order_id}/status",
        f'{{"status":"{status}"}}',
        user_id=uuid.uuid4(),
        path_params={"id": str(order_id)},
    )
    response = await call(handler.update_order_status, request)

    assert response.status_code == 400
    assert decode_error(response)["code"] == constant.ErrorCode.INVALID_STATUS


# A product name is part of the insufficient-stock message, so a name containing "not found" or
# "failed" must not be reported as a 404 or 500.
@pytest.mark.parametrize("name", ["Lost and not found mug", "failed prototype tee", "Plain mug"])
async def test_checkout_insufficient_stock_is_400_whatever_the_product_name(ctrl: Controller, name: str) -> None:
    user_id, product_id = uuid.uuid4(), uuid.uuid4()
    cart_repo = ctrl.mock(CartRepository)
    order_repo = ctrl.mock(OrderRepository)
    cart = Cart(user_id=user_id, items=[CartItem(product_id=product_id, quantity=5)])
    ctrl.expect(cart_repo.get_cart, user_id, returns=cart)
    ctrl.expect(order_repo.create_orders_with_stock, ANY, ANY, raises=StockUnavailableError(product_id, name))
    handler = OrderHandler(order_service(ctrl, order_repo, cart_repo), None)

    request = make_request("POST", "/api/v1/orders", '{"shipping_address":"Jl. Test 1"}', user_id=user_id)
    response = await call(handler.checkout, request)

    assert response.status_code == 400
    error = decode_error(response)
    assert error["code"] == constant.ErrorCode.INSUFFICIENT_STOCK
    assert name in error["message"]


async def test_add_cart_item_insufficient_stock_code(ctrl: Controller) -> None:
    user_id, product_id = uuid.uuid4(), uuid.uuid4()
    product_repo = ctrl.mock(ProductRepository)
    ctrl.expect(product_repo.find_by_id, product_id, returns=Product(id=product_id, stock=1))
    handler = CartHandler(CartService(ctrl.mock(CartRepository), product_repo, None))

    body = f'{{"product_id":"{product_id}","quantity":2}}'
    response = await call(handler.add_item, make_request("POST", "/api/v1/cart/items", body, user_id=user_id))

    assert response.status_code == 400
    assert decode_error(response)["code"] == constant.ErrorCode.INSUFFICIENT_STOCK
