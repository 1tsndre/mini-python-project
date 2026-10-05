import email.utils
import os
import pathlib
import re

import httpx
import pytest

from tests.store_service.test_router import router

MTIME = 1_791_412_050  # Wed, 07 Oct 2026 22:27:30 GMT
LAST_MODIFIED = "Wed, 07 Oct 2026 22:27:30 GMT"
CONTENT = b"0123456789abcdefghij"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    (tmp_path / "products").mkdir()
    photo = tmp_path / "products" / "photo.png"
    photo.write_bytes(CONTENT)
    os.utime(photo, (MTIME, MTIME))
    (tmp_path / "products" / ".gitkeep").write_bytes(b"")
    transport = httpx.ASGITransport(app=router(str(tmp_path)))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def get(client: httpx.AsyncClient, path: str = "/uploads/products/photo.png", **headers: str) -> httpx.Response:
    return await client.get(path, headers=headers)


async def test_whole_file(client: httpx.AsyncClient) -> None:
    response = await get(client)

    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-length"] == "20"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["last-modified"] == LAST_MODIFIED
    assert "etag" not in response.headers


async def test_unknown_extension_is_sniffed(client: httpx.AsyncClient) -> None:
    response = await get(client, "/uploads/products/.gitkeep")

    assert response.headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize(
    ("range_", "content_range", "body"),
    [
        ("bytes=0-3", "bytes 0-3/20", b"0123"),
        ("bytes=16-", "bytes 16-19/20", b"ghij"),
        ("bytes=-3", "bytes 17-19/20", b"hij"),
        ("bytes=18-99", "bytes 18-19/20", b"ij"),
        ("bytes= 2-3 , 30-40", "bytes 2-3/20", b"23"),
    ],
)
async def test_single_range(client: httpx.AsyncClient, range_: str, content_range: str, body: bytes) -> None:
    response = await get(client, Range=range_)

    assert response.status_code == 206
    assert response.headers["content-range"] == content_range
    assert response.content == body


async def test_several_ranges_as_multipart_like_go(client: httpx.AsyncClient) -> None:
    response = await get(client, Range="bytes=0-0,5-6")

    assert response.status_code == 206
    boundary = re.fullmatch(r"multipart/byteranges; boundary=([0-9a-f]{60})", response.headers["content-type"])
    assert boundary is not None
    b = boundary[1]
    assert (
        response.content
        == (
            f"--{b}\r\nContent-Range: bytes 0-0/20\r\nContent-Type: image/png\r\n\r\n0"
            f"\r\n--{b}\r\nContent-Range: bytes 5-6/20\r\nContent-Type: image/png\r\n\r\n56"
            f"\r\n--{b}--\r\n"
        ).encode()
    )
    assert response.headers["content-length"] == str(len(response.content))


@pytest.mark.parametrize(
    ("range_", "body", "content_range"),
    [
        ("bytes=20-", b"invalid range: failed to overlap\n", "bytes */20"),
        ("bytes=abc", b"invalid range\n", None),
        ("items=0-1", b"invalid range\n", None),
        ("bytes=5-1", b"invalid range\n", None),
    ],
)
async def test_unsatisfiable_range(
    client: httpx.AsyncClient, range_: str, body: bytes, content_range: str | None
) -> None:
    response = await get(client, Range=range_)

    assert response.status_code == 416
    assert response.content == body
    assert response.headers.get("content-range") == content_range
    assert "last-modified" not in response.headers


async def test_ranges_larger_than_the_file_are_ignored(client: httpx.AsyncClient) -> None:
    response = await get(client, Range="bytes=0-19,0-19")

    assert response.status_code == 200
    assert response.content == CONTENT


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"If-Modified-Since": LAST_MODIFIED}, 304),
        ({"If-Modified-Since": "Wed, 07 Oct 2026 22:27:29 GMT"}, 200),
        ({"If-None-Match": "*"}, 304),
        ({"If-None-Match": '"x"', "If-Modified-Since": LAST_MODIFIED}, 200),
        ({"If-Match": "*"}, 200),
        ({"If-Match": '"x"'}, 412),
        ({"If-Unmodified-Since": "Wed, 07 Oct 2026 22:27:29 GMT"}, 412),
        ({"If-Unmodified-Since": LAST_MODIFIED}, 200),
    ],
)
async def test_conditional_requests(client: httpx.AsyncClient, headers: dict[str, str], status: int) -> None:
    response = await get(client, **headers)

    assert response.status_code == status
    assert response.headers["last-modified"] == LAST_MODIFIED


@pytest.mark.parametrize(
    ("if_range", "status"),
    [(LAST_MODIFIED, 206), (email.utils.formatdate(MTIME - 1, usegmt=True), 200), ('"etag"', 200)],
)
async def test_if_range(client: httpx.AsyncClient, if_range: str, status: int) -> None:
    response = await get(client, Range="bytes=0-3", **{"If-Range": if_range})

    assert response.status_code == status


@pytest.mark.parametrize(
    ("path", "status", "location"),
    [
        ("/uploads/products", 301, "products/"),
        ("/uploads/products/index.html?x=1", 301, "./?x=1"),
        ("/uploads/products%2F..%2Fproducts", 301, "products/"),
        ("/uploads/%2Fproducts", 404, None),
    ],
)
async def test_redirects(client: httpx.AsyncClient, path: str, status: int, location: str | None) -> None:
    response = await get(client, path)

    assert response.status_code == status
    assert response.headers.get("location") == location


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/uploads/products/missing.png", b"404 page not found\n"),
        ("/uploads/products/photo.png/x", b"404 page not found\n"),
        ("/uploads/products/a%00b.png", b"404 page not found\n"),
    ],
)
async def test_missing_files_are_plain_404s(client: httpx.AsyncClient, path: str, body: bytes) -> None:
    response = await get(client, path)

    assert response.status_code == 404
    assert response.content == body
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_escaped_slash_inside_the_file_path_is_served(client: httpx.AsyncClient) -> None:
    response = await get(client, "/uploads/products%2Fphoto.png")

    assert response.status_code == 200
    assert response.content == CONTENT
