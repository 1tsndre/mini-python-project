import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from store_service.handler.helpers import answer_errors, form_file
from store_service.middleware.body_limit import MaxBodyBytesMiddleware

LIMIT = 1 << 20


async def upload(request: Request) -> Response:
    file = await form_file(request, "image")
    return PlainTextResponse(f"{file.filename} {len(await file.read())}")


def client() -> httpx.AsyncClient:
    app = Starlette(
        routes=[Route("/upload", answer_errors(upload), methods=["POST"])],
        middleware=[Middleware(MaxBodyBytesMiddleware, limit=LIMIT)],
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    ("size", "status", "body"),
    [
        (1 << 10, 200, "photo.png 1024"),
        (2 << 20, 413, "request body too large"),
    ],
    ids=["within limit", "over limit"],
)
async def test_form_file_body_limit(size: int, status: int, body: str) -> None:
    async with client() as c:
        response = await c.post("/upload", files={"image": ("photo.png", b"a" * size)})

    assert response.status_code == status
    assert body in response.text


async def test_form_file_other_errors_are_a_missing_file() -> None:
    async with client() as c:
        response = await c.post("/upload")

    assert response.status_code == 400
    assert response.json()["errors"][0]["message"] == "image file is required"
