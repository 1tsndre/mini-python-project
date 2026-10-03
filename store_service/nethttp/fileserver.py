"""Static files served like Go's http.FileServer(http.Dir(dir)) behind http.StripPrefix, as the Go router
serves /uploads and /docs: the same redirects, errors, conditional requests and byte ranges.
"""

import email.utils
import os
import posixpath
import secrets
import stat
from collections.abc import Awaitable, Callable
from typing import BinaryIO

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from store_service import nethttp
from store_service.nethttp import sniff

# The scope key under which CleanPathMiddleware keeps the request's unescaped path when it routes
# on another spelling of it.
UNESCAPED_PATH = "go.unescaped_path"

_CHUNK_SIZE = 64 * 1024
_INDEX_PAGE = "/index.html"
_ASCII_SPACE = " \t\n\r"

Part = bytes | tuple[int, int]


def file_server(
    prefix: str, directory: str, not_found: Callable[[Request], Response]
) -> Callable[[Request], Awaitable[Response]]:
    """The Go router's fileServer(directory) behind http.StripPrefix(prefix): files are served, but a
    directory is answered with not_found instead of a listing, so the names of all uploaded files,
    including ones no longer referenced, cannot be enumerated.
    """

    async def serve(request: Request) -> Response:
        scope = request.scope
        path = scope.get(UNESCAPED_PATH, scope["path"])
        raw = scope.get("raw_path", b"").decode("latin-1")
        # http.StripPrefix: an unusually escaped path must carry the prefix in its escaped form too.
        if not path.startswith(prefix) or (raw and raw != nethttp.escape_path(path) and not raw.startswith(prefix)):
            return nethttp.not_found()
        url_path = path[len(prefix) :]
        if url_path == "" or url_path.endswith("/"):
            return not_found(request)
        # http.FileServer roots the stripped path. Go's localRedirect checks the escaped path for an
        # escaped slash, which it then still sees only if the stripped path was already rooted.
        escaped_slash = url_path.startswith("/") and "%2f" in raw[len(prefix) :].lower()
        if not url_path.startswith("/"):
            url_path = "/" + url_path
        return _serve_file(request, directory, url_path, escaped_slash)

    return serve


def _serve_file(request: Request, directory: str, url_path: str, escaped_slash: bool) -> Response:
    """net/http's serveFile for one file, opened like http.Dir.Open."""

    def local_redirect(target: str) -> Response:
        return nethttp.local_redirect(escaped_slash, request.url.query, target)

    if url_path.endswith(_INDEX_PAGE):
        return local_redirect("./")

    name = nethttp.clean(url_path)[1:] or "."
    if "\x00" in name:
        return nethttp.error("404 page not found", 404)  # http: invalid or unsafe file path
    try:
        fd = os.open(os.path.join(directory, name), os.O_RDONLY)
    except (FileNotFoundError, NotADirectoryError):
        return nethttp.error("404 page not found", 404)
    except PermissionError:
        return nethttp.error("403 Forbidden", 403)
    except OSError:
        return nethttp.error("500 Internal Server Error", 500)

    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        return nethttp.error("500 Internal Server Error", 500)
    if stat.S_ISDIR(info.st_mode):
        os.close(fd)
        return local_redirect(posixpath.basename(url_path) + "/")

    file = os.fdopen(fd, "rb")
    try:
        return _serve_content(request, file, posixpath.basename(name), info)
    except BaseException:
        file.close()
        raise


def _serve_content(request: Request, file: BinaryIO, filename: str, info: os.stat_result) -> Response:
    """net/http's serveContent: conditional requests, a single range, or several as multipart/byteranges."""
    # Go treats the Unix epoch as an unknown modification time.
    modtime = None if info.st_mtime == 0 else int(info.st_mtime)
    headers: dict[str, str] = {}
    if modtime is not None:
        headers["Last-Modified"] = email.utils.formatdate(info.st_mtime, usegmt=True)

    done = _check_preconditions(request, modtime, headers)
    if done is not None:
        file.close()
        return done
    range_header = request.headers.get("range", "")
    if range_header and _check_if_range(request, modtime) is False:
        range_header = ""

    content_type = sniff.type_by_extension(_extension(filename))
    if not content_type:
        content_type = sniff.detect_content_type(file.read(sniff.SNIFF_LEN))
        file.seek(0)
    headers["Content-Type"] = content_type

    size = info.st_size
    try:
        ranges = _parse_range(range_header, size)
    except _RangeError as e:
        if not (e.no_overlap and size == 0):
            file.close()
            # Like serveError, the error drops the file's Last-Modified.
            return nethttp.error(str(e), 416, {"Content-Range": f"bytes */{size}"} if e.no_overlap else None)
        # Some clients send a Range with every request; an empty file is then served whole.
        ranges = []
    if sum(length for _, length in ranges) > size:
        # More bytes than the file has: probably an attack or a broken client, so the range is ignored.
        ranges = []

    status = 200
    parts: list[Part] = [(0, size)]
    if len(ranges) == 1:
        status = 206
        start, length = ranges[0]
        headers["Content-Range"] = _content_range(start, length, size)
        parts = [ranges[0]]
    elif len(ranges) > 1:
        status = 206
        boundary = secrets.token_hex(30)  # like mime/multipart's random boundary
        parts = []
        for i, (start, length) in enumerate(ranges):
            head = ("\r\n" if i else "") + f"--{boundary}\r\n"
            head += f"Content-Range: {_content_range(start, length, size)}\r\nContent-Type: {content_type}\r\n\r\n"
            parts += [head.encode("latin-1"), (start, length)]
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        headers["Content-Type"] = "multipart/byteranges; boundary=" + boundary

    headers["Accept-Ranges"] = "bytes"
    headers["Content-Length"] = str(sum(len(p) if isinstance(p, bytes) else p[1] for p in parts))
    return _FileResponse(file, parts, status, headers)


class _FileResponse(Response):
    """Sends parts of an open file, and the literal bytes between them, then closes the file."""

    def __init__(self, file: BinaryIO, parts: list[Part], status: int, headers: dict[str, str]) -> None:
        super().__init__(status_code=status, headers=headers)
        self._file = file
        self._parts = parts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            if scope["method"] != "HEAD":
                for part in self._parts:
                    if isinstance(part, bytes):
                        await send({"type": "http.response.body", "body": part, "more_body": True})
                        continue
                    start, remaining = part
                    await anyio.to_thread.run_sync(self._file.seek, start)
                    while remaining > 0:
                        chunk = await anyio.to_thread.run_sync(self._file.read, min(_CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            self._file.close()


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _content_range(start: int, length: int, size: int) -> str:
    return f"bytes {start}-{start + length - 1}/{size}"


# The files have no ETag, so no entity tag in a conditional request ever matches.


def _check_preconditions(request: Request, modtime: int | None, headers: dict[str, str]) -> Response | None:
    """The response that ends the request, or None to serve the file."""
    result = _check_if_match(request)
    if result is None:
        result = _check_if_unmodified_since(request, modtime)
    if result is False:
        return Response(status_code=412, headers=headers)

    none_match = _check_if_none_match(request)
    if none_match is False:
        if request.method in ("GET", "HEAD"):
            return Response(status_code=304, headers=headers)
        return Response(status_code=412, headers=headers)
    if none_match is None and _check_if_modified_since(request, modtime) is False:
        return Response(status_code=304, headers=headers)
    return None


def _check_if_match(request: Request) -> bool | None:
    value = request.headers.get("if-match", "")
    if not value:
        return None
    while value := value.strip(_ASCII_SPACE):
        if value[0] == ",":
            value = value[1:]
            continue
        if value[0] == "*":
            return True
        etag, value = _scan_etag(value)
        if not etag:
            break
    return False


def _check_if_none_match(request: Request) -> bool | None:
    value = request.headers.get("if-none-match", "")
    if not value:
        return None
    while value := value.strip(_ASCII_SPACE):
        if value[0] == ",":
            value = value[1:]
            continue
        if value[0] == "*":
            return False
        etag, value = _scan_etag(value)
        if not etag:
            break
    return True


def _check_if_unmodified_since(request: Request, modtime: int | None) -> bool | None:
    value = request.headers.get("if-unmodified-since", "")
    if not value or modtime is None:
        return None
    since = nethttp.parse_time(value)
    if since is None:
        return None
    return modtime <= since


def _check_if_modified_since(request: Request, modtime: int | None) -> bool | None:
    """False when the file has not changed since the given time."""
    if request.method not in ("GET", "HEAD"):
        return None
    value = request.headers.get("if-modified-since", "")
    if not value or modtime is None:
        return None
    since = nethttp.parse_time(value)
    if since is None:
        return None
    return modtime > since


def _check_if_range(request: Request, modtime: int | None) -> bool | None:
    if request.method not in ("GET", "HEAD"):
        return None
    value = request.headers.get("if-range", "")
    if not value:
        return None
    etag, _ = _scan_etag(value)
    if etag or modtime is None:
        return False
    return nethttp.parse_time(value) == modtime


def _scan_etag(text: str) -> tuple[str, str]:
    """A syntactically valid entity tag at the start of text, and what follows it; ("", "") if there is none."""
    text = text.strip(_ASCII_SPACE)
    start = 2 if text.startswith("W/") else 0
    if len(text) - start < 2 or text[start] != '"':
        return "", ""
    for i in range(start + 1, len(text)):
        c = ord(text[i])
        if c == 0x22:
            return text[: i + 1], text[i + 1 :]
        if not (c == 0x21 or 0x23 <= c <= 0x7E or c >= 0x80):
            return "", ""
    return "", ""


class _RangeError(Exception):
    def __init__(self, message: str, no_overlap: bool = False) -> None:
        super().__init__(message)
        self.no_overlap = no_overlap


def _parse_range(header: str, size: int) -> list[tuple[int, int]]:
    """The (start, length) of each requested range."""
    if not header:
        return []
    if not header.startswith("bytes="):
        raise _RangeError("invalid range")
    ranges: list[tuple[int, int]] = []
    no_overlap = False
    for spec in header[len("bytes=") :].split(","):
        spec = spec.strip(_ASCII_SPACE)
        if not spec:
            continue
        first, dash, last = spec.partition("-")
        if not dash:
            raise _RangeError("invalid range")
        first, last = first.strip(_ASCII_SPACE), last.strip(_ASCII_SPACE)
        if first == "":
            # A suffix range: the last bytes of the file.
            if last == "" or last[0] == "-":
                raise _RangeError("invalid range")
            n = _parse_int64(last)
            if n is None or n < 0:
                raise _RangeError("invalid range")
            start = size - min(n, size)
            ranges.append((start, size - start))
            continue
        start = _parse_int64(first)
        if start is None or start < 0:
            raise _RangeError("invalid range")
        if start >= size:
            no_overlap = True
            continue
        if last == "":
            ranges.append((start, size - start))
            continue
        end = _parse_int64(last)
        if end is None or start > end:
            raise _RangeError("invalid range")
        ranges.append((start, min(end, size - 1) - start + 1))
    if no_overlap and not ranges:
        raise _RangeError("invalid range: failed to overlap", no_overlap=True)
    return ranges


def _parse_int64(text: str) -> int | None:
    """strconv.ParseInt(text, 10, 64), or None on error."""
    digits = text[1:] if text[:1] in ("+", "-") else text
    if not digits or not all("0" <= ch <= "9" for ch in digits):
        return None
    if len(digits.lstrip("0")) > 19:
        return None
    value = -int(digits) if text[0] == "-" else int(digits)
    return value if -(2**63) <= value < 2**63 else None
