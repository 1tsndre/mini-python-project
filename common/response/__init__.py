from dataclasses import dataclass, field
from typing import Any

from starlette.responses import Response as HTTPResponse

from common import gojson

CONTENT_TYPE_JSON = "application/json"


@dataclass
class Pagination:
    current_page: int
    per_page: int
    total_items: int
    total_pages: int


@dataclass
class Meta:
    request_id: str
    timestamp: str
    pagination: Pagination | None = field(default=None, metadata=gojson.OMIT_NIL)


@dataclass
class Error:
    code: str
    field: str = field(default="", metadata=gojson.OMIT_EMPTY)
    message: str = ""


@dataclass
class Response:
    data: Any = field(default=None, metadata=gojson.OMIT_NIL)
    meta: Meta | None = None
    errors: list[Error] = field(default_factory=list, metadata=gojson.OMIT_EMPTY)


def _write_json(status: int, resp: Response) -> HTTPResponse:
    return HTTPResponse(gojson.encode(resp), status_code=status, media_type=CONTENT_TYPE_JSON)


def success(status: int, data: Any, meta: Meta) -> HTTPResponse:
    return _write_json(status, Response(data=data, meta=meta))


def success_with_pagination(status: int, data: Any, meta: Meta, pagination: Pagination) -> HTTPResponse:
    meta.pagination = pagination
    return _write_json(status, Response(data=data, meta=meta))


def error_response(status: int, meta: Meta, *errors: Error) -> HTTPResponse:
    return _write_json(status, Response(meta=meta, errors=list(errors)))


def validation_error(meta: Meta, errors: list[Error]) -> HTTPResponse:
    return _write_json(400, Response(meta=meta, errors=errors))
