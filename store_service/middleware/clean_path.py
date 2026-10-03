import urllib.parse

from starlette.types import ASGIApp, Receive, Scope, Send

from store_service import nethttp
from store_service.nethttp.fileserver import UNESCAPED_PATH


class CleanPathMiddleware:
    """The path handling of Go's http.ServeMux, which the Go router is.

    A request for a path that is not clean ("//", "/./", "/../") is redirected to the clean path. Routes
    are matched segment by segment on the escaped path, so an encoded slash ("%2F") stays inside its
    segment instead of splitting it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        escaped = scope.get("raw_path", b"").decode("latin-1") or scope["path"]
        cleaned = nethttp.clean_request_path(escaped)
        if cleaned != escaped:
            query = scope.get("query_string", b"").decode("latin-1")
            location = cleaned + ("?" + query if query else "")
            await nethttp.redirect(scope["method"], location, 307)(scope, receive, send)
            return

        if "%2f" in escaped.lower():
            segments = (urllib.parse.unquote(segment).replace("/", "%2F") for segment in escaped.split("/"))
            scope = {**scope, "path": "/".join(segments), UNESCAPED_PATH: scope["path"]}
        await self.app(scope, receive, send)
