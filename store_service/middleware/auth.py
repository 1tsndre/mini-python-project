from starlette.requests import Request
from starlette.responses import Response

from common import logger
from common.jwt import JWTManager, TokenType
from common.response import Error, error_response
from store_service import constant
from store_service.middleware.helpers import Handler, Middleware, build_meta


def auth(jwt_manager: JWTManager) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(request: Request) -> Response:
            auth_header = request.headers.get(constant.HEADER_AUTHORIZATION, "")
            if not auth_header:
                return _unauthorized("missing authorization header")

            parts = auth_header.split(" ", 1)
            if len(parts) != 2 or parts[0] != constant.BEARER_SCHEME:
                return _unauthorized("invalid authorization format")

            try:
                claims = jwt_manager.validate_token(parts[1])
            except Exception:  # noqa: BLE001
                return _unauthorized("invalid or expired token")

            if claims.type != TokenType.ACCESS:
                return _unauthorized("invalid token type")

            request.state.user_id = claims.user_id
            request.state.email = claims.email
            request.state.role = claims.role
            logger.with_user_id(claims.user_id)
            return await next_handler(request)

        return handler

    return middleware


def require_role(*roles: constant.Role) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(request: Request) -> Response:
            role = getattr(request.state, "role", None)
            if not isinstance(role, str):
                return error_response(403, build_meta(), Error(constant.ErrorCode.FORBIDDEN, message="forbidden"))
            if role not in roles:
                return error_response(
                    403, build_meta(), Error(constant.ErrorCode.FORBIDDEN, message="insufficient permissions")
                )
            return await next_handler(request)

        return handler

    return middleware


def get_user_id(request: Request) -> str:
    value = getattr(request.state, "user_id", "")
    return value if isinstance(value, str) else ""


def _unauthorized(message: str) -> Response:
    return error_response(401, build_meta(), Error(constant.ErrorCode.UNAUTHORIZED, message=message))
