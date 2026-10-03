import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from store_service.config import duration
from store_service.middleware.helpers import remote_addr

log = structlog.get_logger()


class LoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter_ns()
        status = 200

        async def send_capturing_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capturing_status)
        finally:
            log.info(
                "request completed",
                method=scope["method"],
                path=scope["path"],
                status=status,
                latency=duration.format_nanos(time.perf_counter_ns() - start),
                ip=remote_addr(scope),
            )
