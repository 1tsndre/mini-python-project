from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyTooLargeError(Exception):
    def __init__(self) -> None:
        super().__init__("http: request body too large")


class MaxBodyBytesMiddleware:
    """Caps the size of every request body. Reads past the limit fail, so JSON decoding and multipart
    parsing stop early instead of buffering arbitrarily large payloads.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise BodyTooLargeError()
            return message

        await self.app(scope, limited_receive, send)
