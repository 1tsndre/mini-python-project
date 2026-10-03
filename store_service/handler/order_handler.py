from typing import Protocol

from starlette.requests import Request
from starlette.responses import Response

from common.response import Error, Pagination, success, success_with_pagination
from store_service import constant, pagination
from store_service.handler.helpers import ApiError, decode_json, path_uuid, query, reject, user_id
from store_service.middleware.helpers import build_meta
from store_service.model import CheckoutRequest, PaymentStatusResponse, UpdateOrderStatusRequest
from store_service.service.order_service import OrderService
from store_service.util import conv

INVALID_ID = "invalid order id"


class PaymentStatusClient(Protocol):
    async def get_status(self, order_id: str) -> PaymentStatusResponse: ...


class OrderHandler:
    def __init__(self, service: OrderService, payment: PaymentStatusClient) -> None:
        self._service = service
        self._payment = payment

    async def checkout(self, request: Request) -> Response:
        uid = user_id(request)
        req = await decode_json(request, CheckoutRequest)

        if not req.shipping_address:
            reject("shipping_address", "is required")

        return success(201, await self._service.checkout(uid, req.shipping_address), build_meta())

    async def get_orders(self, request: Request) -> Response:
        uid = user_id(request)
        page = conv.atoi(query(request, "page"))
        per_page = conv.atoi(query(request, "per_page"))

        orders, total = await self._service.get_orders(uid, page, per_page)
        return _page(orders, total, page, per_page)

    async def get_order(self, request: Request) -> Response:
        uid = user_id(request)
        order_id = path_uuid(request, "id", INVALID_ID)
        return success(200, await self._service.get_order_by_id(uid, order_id), build_meta())

    async def cancel_order(self, request: Request) -> Response:
        uid = user_id(request)
        order_id = path_uuid(request, "id", INVALID_ID)
        await self._service.cancel_order(uid, order_id)
        return success(200, {"message": "order cancelled"}, build_meta())

    async def update_order_status(self, request: Request) -> Response:
        uid = user_id(request)
        order_id = path_uuid(request, "id", INVALID_ID)
        req = await decode_json(request, UpdateOrderStatusRequest)

        if not req.status:
            reject("status", "is required")

        await self._service.update_order_status(uid, order_id, req.status)
        return success(200, {"message": "order status updated"}, build_meta())

    async def get_seller_orders(self, request: Request) -> Response:
        uid = user_id(request)
        page = conv.atoi(query(request, "page"))
        per_page = conv.atoi(query(request, "per_page"))

        orders, total = await self._service.get_seller_orders(uid, page, per_page)
        return _page(orders, total, page, per_page)

    async def get_order_payment(self, request: Request) -> Response:
        uid = user_id(request)
        order_id = path_uuid(request, "id", INVALID_ID)

        await self._service.get_order_by_id(uid, order_id)

        try:
            status = await self._payment.get_status(str(order_id))
        except Exception as e:  # noqa: BLE001
            raise ApiError(503, Error(constant.ErrorCode.INTERNAL, message="payment service unavailable")) from e

        return success(200, status, build_meta())


def _page(items: list, total: int, page: int, per_page: int) -> Response:
    page, per_page = pagination.normalize(page, per_page)
    return success_with_pagination(
        200,
        items,
        build_meta(),
        Pagination(
            current_page=page, per_page=per_page, total_items=total, total_pages=pagination.total_pages(total, per_page)
        ),
    )
