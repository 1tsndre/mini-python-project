"""The parts of Go's net/http the Go router relies on, reproduced so the Python service answers the
same way: redirects, plain-text errors, path cleaning and URL escaping, HTTP dates, and static files.
"""

import datetime as dt
import re
import urllib.parse

from starlette.responses import Response

TEXT_UTF8 = "text/plain; charset=utf-8"
HTML_UTF8 = "text/html; charset=utf-8"

# Go's status texts where Python's differ: RFC 9110 renamed these, Go kept the old names.
GO_STATUS_TEXT = {413: "Request Entity Too Large", 416: "Requested Range Not Satisfiable"}
_STATUS_TEXT = {301: "Moved Permanently", 307: "Temporary Redirect"}

# The characters Go leaves unescaped in a URL path, besides letters, digits and "-._~".
_PATH_SAFE = "$&+,/:;=@"


def error(message: str, status: int, headers: dict[str, str] | None = None) -> Response:
    """Like http.Error: the message and a newline as plain text, with nosniff."""
    return Response(
        message + "\n",
        status_code=status,
        headers={**(headers or {}), "Content-Type": TEXT_UTF8, "X-Content-Type-Options": "nosniff"},
    )


def not_found() -> Response:
    """Like http.NotFound."""
    return error("404 page not found", 404)


def redirect(method: str, url: str, status: int) -> Response:
    """Like http.Redirect to an absolute path: a short HTML body for GET, the Content-Type also for HEAD."""
    headers = {"Location": _hex_escape_non_ascii(url)}
    if method in ("GET", "HEAD"):
        headers["Content-Type"] = HTML_UTF8
    body = f'<a href="{html_escape(url)}">{_STATUS_TEXT[status]}</a>.\n\n' if method == "GET" else ""
    return Response(body, status_code=status, headers=headers)


def local_redirect(escaped_slash: bool, query: str, target: str) -> Response:
    """Like net/http's localRedirect: a relative Location keeping the query, or a 404 when the path it
    sees has an escaped slash, since the target could then point elsewhere.
    """
    if escaped_slash:
        return not_found()
    if query:
        target += "?" + query
    return Response(status_code=301, headers={"Location": target})


def _hex_escape_non_ascii(text: str) -> str:
    return "".join(ch if ord(ch) < 0x80 else "".join(f"%{b:02x}" for b in ch.encode()) for ch in text)


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&#34;").replace("'", "&#39;")
    )


def escape_path(path: str) -> str:
    """The path as Go's url.URL writes it (url.PathEscape rules for a whole path)."""
    return urllib.parse.quote(path, safe=_PATH_SAFE)


def clean(path: str) -> str:
    """Go's path.Clean: the shortest equivalent path, processing "." and ".." lexically."""
    if path == "":
        return "."
    rooted = path[0] == "/"
    n = len(path)
    out: list[str] = []
    r = dotdot = 0
    if rooted:
        out.append("/")
        r = dotdot = 1
    while r < n:
        if path[r] == "/":
            r += 1
        elif path[r] == "." and (r + 1 == n or path[r + 1] == "/"):
            r += 1
        elif path[r] == "." and path[r + 1] == "." and (r + 2 == n or path[r + 2] == "/"):
            r += 2
            if len(out) > dotdot:
                w = len(out) - 1
                while w > dotdot and out[w] != "/":
                    w -= 1
                del out[w:]
            elif not rooted:
                if out:
                    out.append("/")
                out.extend("..")
                dotdot = len(out)
        else:
            if (rooted and len(out) != 1) or (not rooted and out):
                out.append("/")
            while r < n and path[r] != "/":
                out.append(path[r])
                r += 1
    return "".join(out) or "."


def clean_request_path(path: str) -> str:
    """net/http's cleanPath: path.Clean for a request path, keeping a trailing slash."""
    if path == "":
        return "/"
    if path[0] != "/":
        path = "/" + path
    cleaned = clean(path)
    if path[-1] == "/" and cleaned != "/":
        cleaned = path if len(path) == len(cleaned) + 1 and path.startswith(cleaned) else cleaned + "/"
    return cleaned


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_SHORT_DAYS = [day[:3] for day in _DAYS]
_TIME_FORMATS = [
    # http.TimeFormat, time.RFC850 and time.ANSIC, the three formats http.ParseTime accepts.
    (
        re.compile(r"(?P<wd>[A-Za-z]{3}), (?P<d>\d\d) (?P<mon>[A-Za-z]{3}) (?P<y>\d{4}) (?P<t>\d\d:\d\d:\d\d) GMT"),
        _SHORT_DAYS,
    ),
    (
        re.compile(r"(?P<wd>[A-Za-z]+), (?P<d>\d\d)-(?P<mon>[A-Za-z]{3})-(?P<y>\d\d) (?P<t>\d\d:\d\d:\d\d) [A-Z]{3}"),
        _DAYS,
    ),
    (
        re.compile(r"(?P<wd>[A-Za-z]{3}) (?P<mon>[A-Za-z]{3}) (?P<d> ?\d\d?) (?P<t>\d\d:\d\d:\d\d) (?P<y>\d{4})"),
        _SHORT_DAYS,
    ),
]


def parse_time(text: str) -> int | None:
    """http.ParseTime as Unix seconds, or None when text is in none of its formats."""
    for pattern, days in _TIME_FORMATS:
        m = pattern.fullmatch(text)
        if m is None or m["wd"].lower() not in days or m["mon"].lower() not in _MONTHS:
            continue
        year = int(m["y"])
        if len(m["y"]) == 2:
            year += 1900 if year >= 69 else 2000
        month = _MONTHS.index(m["mon"].lower()) + 1
        hour, minute, second = (int(part) for part in m["t"].split(":"))
        try:
            moment = dt.datetime(year, month, int(m["d"]), hour, minute, second, tzinfo=dt.UTC)
        except ValueError:
            continue
        return int(moment.timestamp())
    return None
