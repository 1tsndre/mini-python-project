from starlette.requests import Request
from starlette.responses import Response

from common.response import success
from common.upload import Uploader, UploadError
from store_service.handler.helpers import (
    MAX_VARCHAR_MESSAGE,
    bad_request,
    decode_json,
    exceeds_varchar,
    form_file,
    path_uuid,
    reject,
    user_id,
)
from store_service.middleware.helpers import build_meta
from store_service.model import CreateStoreRequest, UpdateStoreRequest
from store_service.service.store_service import StoreService

INVALID_ID = "invalid store id"


class StoreHandler:
    def __init__(self, service: StoreService, uploader: Uploader) -> None:
        self._service = service
        self._uploader = uploader

    async def create_store(self, request: Request) -> Response:
        uid = user_id(request)
        req = await decode_json(request, CreateStoreRequest)

        if not req.name:
            reject("name", "is required")
        if exceeds_varchar(req.name):
            reject("name", MAX_VARCHAR_MESSAGE)

        return success(201, await self._service.create_store(uid, req), build_meta())

    async def get_store(self, request: Request) -> Response:
        store_id = path_uuid(request, "id", INVALID_ID)
        return success(200, await self._service.get_store_by_id(store_id), build_meta())

    async def update_store(self, request: Request) -> Response:
        uid = user_id(request)
        store_id = path_uuid(request, "id", INVALID_ID)
        req = await decode_json(request, UpdateStoreRequest)

        if exceeds_varchar(req.name):
            reject("name", MAX_VARCHAR_MESSAGE)

        return success(200, await self._service.update_store(uid, store_id, req), build_meta())

    async def upload_logo(self, request: Request) -> Response:
        uid = user_id(request)
        store_id = path_uuid(request, "id", INVALID_ID)

        file = await form_file(request, "logo")
        try:
            path = await self._uploader.upload(file, "stores")
        except UploadError as e:
            raise bad_request(str(e)) from e

        try:
            resp = await self._service.update_logo(uid, store_id, path)
        except Exception:
            self._uploader.delete(path)
            raise

        return success(200, resp, build_meta())
