import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common import logger
from store_service import constant


class RequestIDMiddleware:
    """Gives every request an ID (the client's X-Request-ID, or a new UUID) for responses and logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get(constant.HEADER_REQUEST_ID) or str(uuid.uuid4())
        logger.with_request_id(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[constant.HEADER_REQUEST_ID] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)
