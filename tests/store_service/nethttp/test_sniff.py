import pytest

from store_service.nethttp.sniff import detect_content_type, type_by_extension


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"", "text/plain; charset=utf-8"),
        (b"Hello, world\n", "text/plain; charset=utf-8"),
        (b"  <!DOCTYPE html><html>", "text/html; charset=utf-8"),
        (b"<p>x</p>", "text/html; charset=utf-8"),
        (b"<pre>", "text/plain; charset=utf-8"),
        (b'\n<?xml version="1.0"?>', "text/xml; charset=utf-8"),
        (b"%PDF-1.7", "application/pdf"),
        (b"\xef\xbb\xbfhello", "text/plain; charset=utf-8"),
        (b"\xfe\xff\x00h", "text/plain; charset=utf-16be"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a....", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom", "video/mp4"),
        (b"PK\x03\x04", "application/zip"),
        (b"\x00\x01\x02binary", "application/octet-stream"),
    ],
)
def test_detect_content_type_like_go(data: bytes, content_type: str) -> None:
    assert detect_content_type(data) == content_type


def test_type_by_extension_uses_gos_built_in_table() -> None:
    assert type_by_extension(".png") == "image/png"
    assert type_by_extension(".JSON") == "application/json"
    assert type_by_extension(".html") == "text/html; charset=utf-8"
    assert type_by_extension(".gitkeep") == ""
    assert type_by_extension(".txt") == "", "not built into Go; the content is sniffed instead"
