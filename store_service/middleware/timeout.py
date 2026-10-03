import asyncio
import contextlib
import datetime as dt

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common import gojson
from common.response import Error, Response
from store_service import constant
from store_service.config import duration
from store_service.middleware.helpers import build_meta

log = structlog.get_logger()


class TimeoutMiddleware:
    """Answers 504 when a request takes longer than the timeout.

    The handler runs in its own task, like the Go handler goroutine. If it has not started its
    response when the deadline passes, it is cancelled (as Go cancels the request context), the
    client gets the 504 and anything the handler still sends is discarded. A handler that already
    started responding is waited for, so a response is never cut in half.
    """

    def __init__(self, app: ASGIApp, timeout: dt.timedelta) -> None:
        self.app = app
        self.timeout = timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False
        timed_out = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if timed_out:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        handler = asyncio.create_task(self.app(scope, receive, guarded_send))
        done, _ = await asyncio.wait({handler}, timeout=self.timeout.total_seconds())
        if handler in done:
            handler.result()
            return
        if started:
            # The response is already on its way; let the handler finish it.
            await handler
            return

        timed_out = True
        handler.cancel()
        handler.add_done_callback(_consume_result)

        log.error("request timeout", duration=duration.format(self.timeout), path=scope["path"])
        body = gojson.encode(
            Response(meta=build_meta(), errors=[Error(constant.ErrorCode.TIMEOUT, message="request timed out")])
        )
        await send(
            {
                "type": "http.response.start",
                "status": 504,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _consume_result(task: asyncio.Task[None]) -> None:
    with contextlib.suppress(BaseException):
        task.result()
