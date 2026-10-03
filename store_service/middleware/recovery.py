import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common import gojson
from common.response import Error, Response
from store_service import constant
from store_service.middleware.helpers import build_meta

log = structlog.get_logger()


class RecoveryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as e:  # noqa: BLE001
            log.error("panic recovered", error=repr(e), exc_info=True)
            if started:
                return
            body = gojson.encode(
                Response(
                    meta=build_meta(), errors=[Error(constant.ErrorCode.INTERNAL, message="internal server error")]
                )
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
