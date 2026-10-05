import asyncio
import datetime as dt
import json

from starlette.types import Message, Receive, Scope, Send

from common.response import Error, error_response
from store_service import constant
from store_service.middleware.helpers import build_meta
from store_service.middleware.timeout import TimeoutMiddleware


async def serve(app: TimeoutMiddleware) -> tuple[int, dict[str, str], bytes]:
    """Runs one GET request through app and returns what the client receives."""
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    await app(scope, receive, send)

    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert len(starts) == 1, messages
    headers = {k.decode(): v.decode() for k, v in starts[0]["headers"]}
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return starts[0]["status"], headers, body


def timeout(seconds: float, app: object) -> TimeoutMiddleware:
    return TimeoutMiddleware(app, dt.timedelta(seconds=seconds))


async def test_passes_through_fast_response() -> None:
    async def handler(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 201, "headers": [(b"x-handler", b"yes")]})
        await send({"type": "http.response.body", "body": b"created"})

    status, headers, body = await serve(timeout(1, handler))

    assert status == 201
    assert headers["x-handler"] == "yes"
    assert body == b"created"


async def test_slow_handler_gets_504() -> None:
    async def handler(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Like a Go handler that ignores its context, it still tries to answer after the deadline.
            await send({"type": "http.response.start", "status": 200, "headers": [(b"x-late", b"should-not-leak")]})
            await send({"type": "http.response.body", "body": b"late body"})
            raise

    status, headers, body = await serve(timeout(0.01, handler))

    assert status == 504
    assert "x-late" not in headers
    assert json.loads(body)["errors"][0]["code"] == constant.ErrorCode.TIMEOUT


async def test_handler_is_cancelled_at_the_deadline() -> None:
    # As Go cancels the request context, so database and gRPC calls stop instead of running on.
    cancelled = asyncio.Event()

    async def handler(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await serve(timeout(0.01, handler))
    await asyncio.wait_for(cancelled.wait(), 1)


async def test_started_response_is_completed_not_replaced() -> None:
    async def handler(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0.1)  # past the deadline
        await send({"type": "http.response.body", "body": b"finished"})

    status, _, body = await serve(timeout(0.05, handler))

    # The middleware must not return before the handler finished writing.
    assert status == 200
    assert body == b"finished"


async def test_response_is_either_the_handlers_or_the_504_never_a_mix() -> None:
    # A handler whose database call returns just as the deadline passes answers at the same moment
    # the middleware does; whichever starts first wins and the other is dropped whole.
    async def handler(scope: Scope, receive: Receive, send: Send) -> None:
        await asyncio.sleep(0.001)
        response = error_response(500, build_meta(), Error(constant.ErrorCode.INTERNAL, message="db error"))
        await response(scope, receive, send)

    want_code = {504: constant.ErrorCode.TIMEOUT, 500: constant.ErrorCode.INTERNAL}
    app = timeout(0.001, handler)
    for _ in range(10):
        results = await asyncio.gather(*(serve(app) for _ in range(200)))
        for status, _, body in results:
            assert status in want_code
            assert json.loads(body)["errors"][0]["code"] == want_code[status]
