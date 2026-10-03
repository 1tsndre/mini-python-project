from starlette.requests import Request
from starlette.responses import Response

from common.response import success
from store_service.handler.helpers import check, decode_json, field_error, path_uuid, reject, user_id
from store_service.middleware.helpers import build_meta
from store_service.model import AddCartItemRequest, UpdateCartItemRequest
from store_service.service.cart_service import CartService

INVALID_PRODUCT_ID = "invalid product id"


class CartHandler:
    def __init__(self, service: CartService) -> None:
        self._service = service

    async def get_cart(self, request: Request) -> Response:
        uid = user_id(request)
        return success(200, await self._service.get_cart(uid), build_meta())

    async def add_item(self, request: Request) -> Response:
        uid = user_id(request)
        req = await decode_json(request, AddCartItemRequest)

        errors = []
        if not req.product_id:
            errors.append(field_error("product_id", "is required"))
        if req.quantity <= 0:
            errors.append(field_error("quantity", "must be greater than 0"))
        check(errors)

        return success(200, await self._service.add_item(uid, req), build_meta())

    async def update_item(self, request: Request) -> Response:
        uid = user_id(request)
        product_id = path_uuid(request, "product_id", INVALID_PRODUCT_ID)
        req = await decode_json(request, UpdateCartItemRequest)

        if req.quantity <= 0:
            reject("quantity", "must be greater than 0")

        return success(200, await self._service.update_item(uid, product_id, req), build_meta())

    async def remove_item(self, request: Request) -> Response:
        uid = user_id(request)
        product_id = path_uuid(request, "product_id", INVALID_PRODUCT_ID)
        return success(200, await self._service.remove_item(uid, product_id), build_meta())
