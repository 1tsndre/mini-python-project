import datetime as dt
import pathlib
import urllib.parse
import uuid
from unittest.mock import MagicMock

import httpx
import pytest
from starlette.applications import Starlette

from common.jwt import JWTManager
from store_service import constant
from store_service.config import RateConfig
from store_service.handler.product_handler import ProductHandler
from store_service.router import Handlers, create_app

JWT_MANAGER = JWTManager("router-test-secret-of-at-least-32-bytes", dt.timedelta(minutes=15), dt.timedelta(hours=1))


def router(upload_dir: str = ".") -> Starlette:
    # The routes under test need none of the handlers. The Redis mock fails every call, and a rate
    # limiter that cannot reach Redis lets requests through.
    handlers = Handlers(*(MagicMock() for _ in range(7)))
    return create_app(
        handlers, JWT_MANAGER, MagicMock(), upload_dir, 1 << 20, dt.timedelta(seconds=5), RateConfig(60, 120, 10)
    )


def client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    ("path", "status", "body"),
    [
        ("/uploads/products/photo.png", 200, "png-bytes"),
        ("/uploads/", 404, None),
        ("/uploads/products/", 404, None),
        # A directory is redirected to its trailing-slash form, which is refused above.
        ("/uploads/products", 301, None),
    ],
)
async def test_uploads_serve_files_but_not_directory_listings(
    tmp_path: pathlib.Path, path: str, status: int, body: str | None
) -> None:
    (tmp_path / "products").mkdir()
    (tmp_path / "products" / "photo.png").write_bytes(b"png-bytes")

    async with client(router(str(tmp_path))) as c:
        response = await c.get(path)

    assert response.status_code == status
    if body is not None:
        assert response.text == body
    else:
        assert "photo.png" not in response.text, "directory contents must not be listed"


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/nope"), ("DELETE", "/api/v1/products"), ("OPTIONS", "/api/v1/products"), ("GET", "/api/v1/products/")],
    ids=["unknown route", "unknown method", "options", "trailing slash"],
)
async def test_unmatched_requests_get_the_json_404(method: str, path: str) -> None:
    # Like the Go router's catch-all: a wrong method is a 404 too, never a 405.
    async with client(router()) as c:
        response = await c.request(method, path)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["errors"] == [{"code": "NOT_FOUND", "message": "not found"}]


async def test_uploads_and_docs_without_a_slash_redirect() -> None:
    async with client(router()) as c:
        for path in ("/uploads", "/docs"):
            response = await c.get(path)
            assert response.status_code == 307
            assert response.headers["location"] == path + "/"


async def test_protected_routes_check_the_token_then_the_role() -> None:
    async with client(router()) as c:
        response = await c.get("/api/v1/cart")
        assert response.status_code == 401
        assert response.json()["errors"][0]["message"] == "missing authorization header"

        buyer = JWT_MANAGER.generate_token_pair(str(uuid.uuid4()), "buyer@example.com", constant.Role.BUYER)
        response = await c.get("/api/v1/seller/orders", headers={"Authorization": "Bearer " + buyer.access_token})
        assert response.status_code == 403
        assert response.json()["errors"][0]["message"] == "insufficient permissions"


async def raw_request(app: Starlette, method: str, target: str) -> tuple[int, dict[str, str], bytes]:
    """Sends target exactly as written, the way uvicorn passes it on; HTTP clients would normalize it."""
    raw_path, _, query = target.partition("?")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": urllib.parse.unquote(raw_path),
        "raw_path": raw_path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(m.get("body", b"") for m in messages[1:])


@pytest.mark.parametrize(
    ("method", "target", "location"),
    [
        ("GET", "//health", "/health"),
        ("GET", "/api/v1/../v1/categories?a=%20b", "/api/v1/categories?a=%20b"),
        ("GET", "/a/../b%20c", "/b%20c"),
        ("HEAD", "/./health", "/health"),
        ("POST", "/a/../api/v1/auth/login", "/api/v1/auth/login"),
        ("GET", "//nope", "/nope"),
    ],
)
async def test_unclean_paths_redirect_to_the_clean_path(method: str, target: str, location: str) -> None:
    # Like Go's ServeMux: before any route is matched, unknown routes included.
    status, headers, body = await raw_request(router(), method, target)

    assert status == 307
    assert headers["location"] == location
    if method == "GET":
        assert body == f'<a href="{location}">Temporary Redirect</a>.\n\n'.encode()
    else:
        assert body == b""
        assert ("content-type" in headers) == (method == "HEAD")


async def test_an_escaped_slash_stays_inside_its_path_segment() -> None:
    # The route still matches, with an ID that is not a UUID, as in Go.
    handlers = Handlers(*(MagicMock() for _ in range(7)))
    handlers.product = ProductHandler(None, None)
    app = create_app(handlers, JWT_MANAGER, MagicMock(), ".", 1 << 20, dt.timedelta(seconds=5), RateConfig(60, 120, 10))

    status, _, body = await raw_request(app, "GET", "/api/v1/products/a%2Fb")

    assert status == 400
    assert b'"message":"invalid product id"' in body
