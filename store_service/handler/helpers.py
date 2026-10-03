import uuid

from python_multipart.multipart import parse_options_header
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import Response

from common import gojson
from common.response import Error, error_response
from store_service import constant
from store_service.middleware.auth import get_user_id
from store_service.middleware.body_limit import BodyTooLargeError
from store_service.middleware.helpers import Handler, build_meta
from store_service.service.errors import (
    ConflictError,
    ForbiddenError,
    InsufficientStockError,
    InternalError,
    InvalidStatusError,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from store_service.util import uuids

MAX_VARCHAR_MESSAGE = f"maximum {constant.MAX_VARCHAR_LENGTH} characters"
MAX_PASSWORD_MESSAGE = f"maximum {constant.MAX_PASSWORD_BYTES} bytes"


SERVICE_ERRORS: dict[type[ServiceError], tuple[int, constant.ErrorCode]] = {
    NotFoundError: (404, constant.ErrorCode.NOT_FOUND),
    ForbiddenError: (403, constant.ErrorCode.FORBIDDEN),
    ConflictError: (409, constant.ErrorCode.CONFLICT),
    ValidationError: (400, constant.ErrorCode.VALIDATION),
    UnauthorizedError: (401, constant.ErrorCode.UNAUTHORIZED),
    InternalError: (500, constant.ErrorCode.INTERNAL),
    InsufficientStockError: (400, constant.ErrorCode.INSUFFICIENT_STOCK),
    InvalidStatusError: (400, constant.ErrorCode.INVALID_STATUS),
}


class ApiError(Exception):
    def __init__(self, status: int, *errors: Error) -> None:
        super().__init__(errors[0].message)
        self.status = status
        self.errors = errors


def bad_request(message: str) -> ApiError:
    return ApiError(400, Error(constant.ErrorCode.VALIDATION, message=message))


def field_error(field_name: str, message: str) -> Error:
    return Error(constant.ErrorCode.VALIDATION, field=field_name, message=message)


def check(errors: list[Error]) -> None:
    if errors:
        raise ApiError(400, *errors)


def reject(field_name: str, message: str) -> None:
    raise ApiError(400, field_error(field_name, message))


def answer_errors(handler: Handler) -> Handler:
    """Turns the errors a handler raises into their JSON responses, inside the route's middleware."""

    async def wrapped(request: Request) -> Response:
        try:
            return await handler(request)
        except ApiError as e:
            return error_response(e.status, build_meta(), *e.errors)
        except ServiceError as e:
            status, code = SERVICE_ERRORS[type(e)]
            return error_response(status, build_meta(), Error(code, message=e.message))

    return wrapped


def exceeds_varchar(text: str) -> bool:
    """Whether text is longer than a VARCHAR(255) column holds; PostgreSQL counts characters."""
    return len(text) > constant.MAX_VARCHAR_LENGTH


async def decode_json[T](request: Request, cls: type[T]) -> T:
    """Decodes the request body like Go's json.NewDecoder(r.Body).Decode: the Content-Type is not
    checked, field names match case-insensitively, unknown fields are ignored and only the first
    JSON value is read. Anything else is a 400 "invalid request body".
    """
    try:
        return gojson.bind(gojson.decode(await request.body()), cls)
    except (gojson.DecodeError, BodyTooLargeError) as e:
        raise bad_request("invalid request body") from e


def user_id(request: Request) -> uuid.UUID:
    """The authenticated user's ID; a token whose user_id is not a UUID is an invalid user."""
    parsed = uuids.parse(get_user_id(request))
    if parsed is None:
        raise ApiError(401, Error(constant.ErrorCode.UNAUTHORIZED, message="invalid user"))
    return parsed


def path_uuid(request: Request, name: str, message: str) -> uuid.UUID:
    parsed = uuids.parse(request.path_params.get(name, ""))
    if parsed is None:
        raise bad_request(message)
    return parsed


def query(request: Request, name: str) -> str:
    """The first value of a query parameter, like Go's url.Values.Get."""
    values = request.query_params.getlist(name)
    return values[0] if values else ""


async def form_file(request: Request, field_name: str) -> UploadFile:
    """The uploaded file of a multipart/form-data field, like Go's r.FormFile."""
    missing = bad_request(f"{field_name} file is required")
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    if content_type != b"multipart/form-data" or not params.get(b"boundary"):
        raise missing
    try:
        form = await request.form()
    except BodyTooLargeError as e:
        raise ApiError(413, Error(constant.ErrorCode.VALIDATION, message="request body too large")) from e
    except Exception as e:  # noqa: BLE001 - a malformed form is a missing file, as in Go
        raise missing from e
    for value in form.getlist(field_name):
        # A part without a file name is a plain form value, not a file.
        if isinstance(value, UploadFile) and value.filename:
            return value
    raise missing
