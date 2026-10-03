"""Go's http.DetectContentType (the WHATWG MIME sniffing rules) and mime.TypeByExtension's built-in table."""

import struct
from collections.abc import Callable

SNIFF_LEN = 512

# mime.TypeByExtension without system files, as in the Go service's Alpine image.
BUILTIN_TYPES = {
    ".avif": "image/avif",
    ".css": "text/css; charset=utf-8",
    ".gif": "image/gif",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".mjs": "text/javascript; charset=utf-8",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".webp": "image/webp",
    ".xml": "text/xml; charset=utf-8",
}


def type_by_extension(ext: str) -> str:
    """The type for a file extension such as ".png", or "" when it is unknown."""
    return BUILTIN_TYPES.get(ext) or BUILTIN_TYPES.get(ext.lower(), "")


_WHITESPACE = b"\t\n\x0c\r "
_HTML_TAGS = [
    b"<!DOCTYPE HTML", b"<HTML", b"<HEAD", b"<SCRIPT", b"<IFRAME", b"<H1", b"<DIV", b"<FONT", b"<TABLE", b"<A",
    b"<STYLE", b"<TITLE", b"<B", b"<BODY", b"<BR", b"<P", b"<!--",
]  # fmt: skip


def _html(tag: bytes) -> Callable[[bytes, int], str]:
    def match(data: bytes, first_non_ws: int) -> str:
        data = data[first_non_ws:]
        if len(data) < len(tag) + 1:
            return ""
        for i, b in enumerate(tag):
            db = data[i] & 0xDF if 0x41 <= b <= 0x5A else data[i]
            if b != db:
                return ""
        return "text/html; charset=utf-8" if data[len(tag)] in b" >" else ""

    return match


def _masked(mask: bytes, pattern: bytes, content_type: str, skip_ws: bool = False) -> Callable[[bytes, int], str]:
    def match(data: bytes, first_non_ws: int) -> str:
        if skip_ws:
            data = data[first_non_ws:]
        if len(data) < len(pattern):
            return ""
        return content_type if all(data[i] & mask[i] == p for i, p in enumerate(pattern)) else ""

    return match


def _exact(signature: bytes, content_type: str) -> Callable[[bytes, int], str]:
    return lambda data, first_non_ws: content_type if data.startswith(signature) else ""


def _mp4(data: bytes, first_non_ws: int) -> str:
    if len(data) < 12:
        return ""
    (box_size,) = struct.unpack(">I", data[:4])
    if len(data) < box_size or box_size % 4 != 0 or data[4:8] != b"ftyp":
        return ""
    for start in range(8, box_size, 4):
        if start != 12 and data[start : start + 3] == b"mp4":
            return "video/mp4"
    return ""


def _text(data: bytes, first_non_ws: int) -> str:
    for b in data[first_non_ws:]:
        if b <= 0x08 or b == 0x0B or 0x0E <= b <= 0x1A or 0x1C <= b <= 0x1F:
            return ""
    return "text/plain; charset=utf-8"


_SIGNATURES: list[Callable[[bytes, int], str]] = [
    *(_html(tag) for tag in _HTML_TAGS),
    _masked(b"\xff" * 5, b"<?xml", "text/xml; charset=utf-8", skip_ws=True),
    _exact(b"%PDF-", "application/pdf"),
    _exact(b"%!PS-Adobe-", "application/postscript"),
    _masked(b"\xff\xff\x00\x00", b"\xfe\xff\x00\x00", "text/plain; charset=utf-16be"),
    _masked(b"\xff\xff\x00\x00", b"\xff\xfe\x00\x00", "text/plain; charset=utf-16le"),
    _masked(b"\xff\xff\xff\x00", b"\xef\xbb\xbf\x00", "text/plain; charset=utf-8"),
    _exact(b"\x00\x00\x01\x00", "image/x-icon"),
    _exact(b"\x00\x00\x02\x00", "image/x-icon"),
    _exact(b"BM", "image/bmp"),
    _exact(b"GIF87a", "image/gif"),
    _exact(b"GIF89a", "image/gif"),
    _masked(b"\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff\xff\xff", b"RIFF\x00\x00\x00\x00WEBPVP", "image/webp"),
    _exact(b"\x89PNG\r\n\x1a\n", "image/png"),
    _exact(b"\xff\xd8\xff", "image/jpeg"),
    _masked(b"\xff\xff\xff\xff", b".snd", "audio/basic"),
    _masked(b"\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff", b"FORM\x00\x00\x00\x00AIFF", "audio/aiff"),
    _masked(b"\xff\xff\xff", b"ID3", "audio/mpeg"),
    _masked(b"\xff" * 5, b"OggS\x00", "application/ogg"),
    _masked(b"\xff" * 8, b"MThd\x00\x00\x00\x06", "audio/midi"),
    _masked(b"\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff", b"RIFF\x00\x00\x00\x00AVI ", "video/avi"),
    _masked(b"\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff", b"RIFF\x00\x00\x00\x00WAVE", "audio/wave"),
    _mp4,
    _exact(b"\x1a\x45\xdf\xa3", "video/webm"),
    _masked(b"\x00" * 34 + b"\xff\xff", b"\x00" * 34 + b"LP", "application/vnd.ms-fontobject"),
    _exact(b"\x00\x01\x00\x00", "font/ttf"),
    _exact(b"OTTO", "font/otf"),
    _exact(b"ttcf", "font/collection"),
    _exact(b"wOFF", "font/woff"),
    _exact(b"wOF2", "font/woff2"),
    _exact(b"\x1f\x8b\x08", "application/x-gzip"),
    _exact(b"PK\x03\x04", "application/zip"),
    _exact(b"Rar!\x1a\x07\x00", "application/x-rar-compressed"),
    _exact(b"Rar!\x1a\x07\x01\x00", "application/x-rar-compressed"),
    _exact(b"\x00asm", "application/wasm"),
    _text,
]


def detect_content_type(data: bytes) -> str:
    """The content type of data from its first 512 bytes, like http.DetectContentType."""
    data = data[:SNIFF_LEN]
    first_non_ws = 0
    while first_non_ws < len(data) and data[first_non_ws] in _WHITESPACE:
        first_non_ws += 1
    for signature in _SIGNATURES:
        content_type = signature(data, first_non_ws)
        if content_type:
            return content_type
    return "application/octet-stream"
