from starlette.requests import Request
from starlette.responses import Response

from common.response import success
from store_service.handler.helpers import MAX_VARCHAR_MESSAGE, decode_json, exceeds_varchar, path_uuid, reject
from store_service.middleware.helpers import build_meta
from store_service.model import CreateCategoryRequest, UpdateCategoryRequest
from store_service.service.category_service import CategoryService

INVALID_ID = "invalid category id"


class CategoryHandler:
    def __init__(self, service: CategoryService) -> None:
        self._service = service

    async def create_category(self, request: Request) -> Response:
        req = await decode_json(request, CreateCategoryRequest)

        if not req.name:
            reject("name", "is required")
        if exceeds_varchar(req.name):
            reject("name", MAX_VARCHAR_MESSAGE)

        return success(201, await self._service.create_category(req), build_meta())

    async def get_categories(self, request: Request) -> Response:
        return success(200, await self._service.get_all_categories(), build_meta())

    async def update_category(self, request: Request) -> Response:
        category_id = path_uuid(request, "id", INVALID_ID)
        req = await decode_json(request, UpdateCategoryRequest)

        if exceeds_varchar(req.name):
            reject("name", MAX_VARCHAR_MESSAGE)

        return success(200, await self._service.update_category(category_id, req), build_meta())

    async def delete_category(self, request: Request) -> Response:
        category_id = path_uuid(request, "id", INVALID_ID)
        await self._service.delete_category(category_id)
        return success(200, {"message": "category deleted"}, build_meta())
