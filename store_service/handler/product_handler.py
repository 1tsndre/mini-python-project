from starlette.requests import Request
from starlette.responses import Response

from common.response import Error, Pagination, success, success_with_pagination
from common.upload import Uploader, UploadError
from store_service import pagination
from store_service.handler.helpers import (
    MAX_VARCHAR_MESSAGE,
    bad_request,
    check,
    decode_json,
    exceeds_varchar,
    field_error,
    form_file,
    path_uuid,
    query,
    reject,
    user_id,
)
from store_service.middleware.helpers import build_meta
from store_service.model import CreateProductRequest, ProductFilter, UpdateProductRequest
from store_service.service.product_service import ProductService
from store_service.util import conv, uuids

INVALID_ID = "invalid product id"


class ProductHandler:
    def __init__(self, service: ProductService, uploader: Uploader) -> None:
        self._service = service
        self._uploader = uploader

    async def create_product(self, request: Request) -> Response:
        uid = user_id(request)
        req = await decode_json(request, CreateProductRequest)

        errors = []
        if not req.name:
            errors.append(field_error("name", "is required"))
        elif exceeds_varchar(req.name):
            errors.append(field_error("name", MAX_VARCHAR_MESSAGE))
        if not req.price:
            errors.append(field_error("price", "is required"))
        if not req.category_id:
            errors.append(field_error("category_id", "is required"))
        check(errors)

        return success(201, await self._service.create_product(uid, req), build_meta())

    async def get_products(self, request: Request) -> Response:
        page = conv.atoi(query(request, "page"))
        per_page = conv.atoi(query(request, "per_page"))

        # The IDs are compared against UUID columns, where PostgreSQL rejects anything that is not
        # a UUID; pass them on in canonical form or reject them as a 400.
        category_id, store_id, field_errs = parse_id_filters(query(request, "category_id"), query(request, "store_id"))
        check(field_errs)

        product_filter = ProductFilter(
            category_id=category_id,
            store_id=store_id,
            search=query(request, "search"),
            min_price=query(request, "min_price"),
            max_price=query(request, "max_price"),
            sort_by=query(request, "sort_by"),
            sort_order=query(request, "sort_order"),
            page=page,
            per_page=per_page,
        )

        products, total = await self._service.get_products(product_filter)

        page, per_page = pagination.normalize(page, per_page)
        return success_with_pagination(
            200,
            products,
            build_meta(),
            Pagination(
                current_page=page,
                per_page=per_page,
                total_items=total,
                total_pages=pagination.total_pages(total, per_page),
            ),
        )

    async def get_product(self, request: Request) -> Response:
        product_id = path_uuid(request, "id", INVALID_ID)
        return success(200, await self._service.get_product_by_id(product_id), build_meta())

    async def update_product(self, request: Request) -> Response:
        uid = user_id(request)
        product_id = path_uuid(request, "id", INVALID_ID)
        req = await decode_json(request, UpdateProductRequest)

        if exceeds_varchar(req.name):
            reject("name", MAX_VARCHAR_MESSAGE)

        return success(200, await self._service.update_product(uid, product_id, req), build_meta())

    async def delete_product(self, request: Request) -> Response:
        uid = user_id(request)
        product_id = path_uuid(request, "id", INVALID_ID)
        await self._service.delete_product(uid, product_id)
        return success(200, {"message": "product deleted"}, build_meta())

    async def upload_image(self, request: Request) -> Response:
        uid = user_id(request)
        product_id = path_uuid(request, "id", INVALID_ID)

        file = await form_file(request, "image")
        try:
            path = await self._uploader.upload(file, "products")
        except UploadError as e:
            raise bad_request(str(e)) from e

        try:
            resp = await self._service.update_image(uid, product_id, path)
        except Exception:
            self._uploader.delete(path)
            raise

        return success(200, resp, build_meta())


def parse_id_filters(category_id: str, store_id: str) -> tuple[str, str, list[Error]]:
    field_errs: list[Error] = []

    def canonical(field_name: str, value: str) -> str:
        if not value:
            return ""
        parsed = uuids.parse(value)
        if parsed is None:
            field_errs.append(field_error(field_name, "must be a valid UUID"))
            return ""
        return str(parsed)

    return canonical("category_id", category_id), canonical("store_id", store_id), field_errs
