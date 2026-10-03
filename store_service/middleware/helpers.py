import datetime as dt
import ipaddress
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Scope

from common import logger
from common.response import Meta

Handler = Callable[[Request], Awaitable[Response]]
Middleware = Callable[[Handler], Handler]


def build_meta(request: Request | None = None) -> Meta:
    return Meta(request_id=logger.get_request_id(), timestamp=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


def chain(handler: Handler, *middlewares: Middleware) -> Handler:
    """Wraps handler so the first middleware runs first, like the Go router's Chain."""
    for middleware in reversed(middlewares):
        handler = middleware(handler)
    return handler


def client_addr(scope: Scope) -> tuple[str, int]:
    """The client's IP address and port as Go reports them: an IPv4 client of the dual-stack
    listener appears as plain IPv4, not as an IPv4-mapped IPv6 address.
    """
    client = scope.get("client") or ("", 0)
    host, port = client[0], client[1]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host, port
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped), port
    return host, port


def remote_addr(scope: Scope) -> str:
    """The client's address like Go's Request.RemoteAddr: "127.0.0.1:5000", or "[::1]:5000"."""
    host, port = client_addr(scope)
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
