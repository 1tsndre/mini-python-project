"""Calls handlers directly, like Go's httptest: a request with the authenticated user and path
parameters already set, answered through the same error mapping the router applies.
"""

import json
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message

from store_service.handler.helpers import answer_errors
from store_service.middleware.helpers import Handler


def make_request(
    method: str,
    target: str,
    body: bytes | str = b"",
    *,
    user_id: uuid.UUID | None = None,
    path_params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    path, _, query = target.partition("?")
    data = body.encode() if isinstance(body, str) else body
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "path_params": path_params or {},
        "client": ("192.0.2.1", 1234),
        "server": ("example.com", 80),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
    }
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": data, "more_body": False}

    request = Request(scope, receive)
    if user_id is not None:
        request.state.user_id = str(user_id)
    return request


async def call(handler: Handler, request: Request) -> Response:
    return await answer_errors(handler)(request)


def json_body(value: Any) -> str:
    return json.dumps(value)


def decode_error(response: Response) -> dict[str, Any]:
    body = json.loads(response.body)
    assert len(body["errors"]) == 1, body
    return body["errors"][0]
