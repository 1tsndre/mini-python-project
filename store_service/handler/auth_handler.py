import re

from starlette.requests import Request
from starlette.responses import Response

from common.response import success
from store_service import constant
from store_service.handler.helpers import (
    MAX_PASSWORD_MESSAGE,
    MAX_VARCHAR_MESSAGE,
    check,
    decode_json,
    exceeds_varchar,
    field_error,
    reject,
)
from store_service.middleware.helpers import build_meta
from store_service.model import LoginRequest, RefreshRequest, RegisterRequest
from store_service.service.auth_service import AuthService

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


class AuthHandler:
    def __init__(self, service: AuthService) -> None:
        self._service = service

    async def register(self, request: Request) -> Response:
        req = await decode_json(request, RegisterRequest)

        errors = []
        if not req.email:
            errors.append(field_error("email", "is required"))
        elif exceeds_varchar(req.email):
            errors.append(field_error("email", MAX_VARCHAR_MESSAGE))
        elif not EMAIL_REGEX.fullmatch(req.email):
            errors.append(field_error("email", "invalid email format"))
        if not req.password:
            errors.append(field_error("password", "is required"))
        elif len(req.password.encode()) < 6:
            errors.append(field_error("password", "minimum 6 characters"))
        elif len(req.password.encode()) > constant.MAX_PASSWORD_BYTES:
            errors.append(field_error("password", MAX_PASSWORD_MESSAGE))
        if not req.name:
            errors.append(field_error("name", "is required"))
        elif exceeds_varchar(req.name):
            errors.append(field_error("name", MAX_VARCHAR_MESSAGE))
        check(errors)

        return success(201, await self._service.register(req), build_meta())

    async def login(self, request: Request) -> Response:
        req = await decode_json(request, LoginRequest)

        errors = []
        if not req.email:
            errors.append(field_error("email", "is required"))
        if not req.password:
            errors.append(field_error("password", "is required"))
        check(errors)

        return success(200, await self._service.login(req), build_meta())

    async def refresh(self, request: Request) -> Response:
        req = await decode_json(request, RefreshRequest)

        if not req.refresh_token:
            reject("refresh_token", "is required")

        return success(200, await self._service.refresh_token(req), build_meta())
