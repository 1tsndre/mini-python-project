import pytest

from store_service import nethttp


@pytest.mark.parametrize(
    ("path", "cleaned"),
    [
        ("", "."),
        ("/", "/"),
        ("abc", "abc"),
        ("a//c", "a/c"),
        ("a/./c", "a/c"),
        ("a/b/../c", "a/c"),
        ("../../a", "../../a"),
        ("a/../../b", "../b"),
        ("/../a", "/a"),
        ("/a/b/..", "/a"),
        ("a/", "a"),
    ],
)
def test_clean_like_go_path_clean(path: str, cleaned: str) -> None:
    assert nethttp.clean(path) == cleaned


@pytest.mark.parametrize(
    ("path", "cleaned"),
    [
        ("", "/"),
        ("//", "/"),
        ("/a//b", "/a/b"),
        ("/a/./b/", "/a/b/"),
        ("/a/b//", "/a/b/"),
        ("a/b", "/a/b"),
        ("/..", "/"),
    ],
)
def test_clean_request_path_keeps_a_trailing_slash(path: str, cleaned: str) -> None:
    assert nethttp.clean_request_path(path) == cleaned


def test_escape_path_like_go_url() -> None:
    assert nethttp.escape_path("/a b/ü/$&+,:;=@?#%") == "/a%20b/%C3%BC/$&+,:;=@%3F%23%25"


@pytest.mark.parametrize(
    "text",
    [
        "Wed, 07 Oct 2026 22:27:30 GMT",
        "Wednesday, 07-Oct-26 22:27:30 GMT",
        "Wed Oct  7 22:27:30 2026",
        "Wed Oct 7 22:27:30 2026",
        "wed, 07 OCT 2026 22:27:30 GMT",
    ],
)
def test_parse_time_accepts_the_three_http_formats(text: str) -> None:
    assert nethttp.parse_time(text) == 1_791_412_050


@pytest.mark.parametrize(
    "text",
    [
        "Wed, 7 Oct 2026 22:27:30 GMT",
        "Wed, 31 Feb 2026 22:27:30 GMT",
        "Wed, 07 Oct 2026 24:00:00 GMT",
        "Wed, 07 Oct 2026 22:27:30 +0000",
        "Xyz, 07 Oct 2026 22:27:30 GMT",
        "yesterday",
    ],
)
def test_parse_time_rejects_anything_else(text: str) -> None:
    assert nethttp.parse_time(text) is None


def test_redirect_like_go() -> None:
    get = nethttp.redirect("GET", "/a?b=<c>", 307)
    assert get.headers["location"] == "/a?b=<c>"
    assert get.headers["content-type"] == "text/html; charset=utf-8"
    assert get.body == b'<a href="/a?b=&lt;c&gt;">Temporary Redirect</a>.\n\n'

    head = nethttp.redirect("HEAD", "/a", 307)
    assert head.headers["content-type"] == "text/html; charset=utf-8"
    assert head.body == b""

    post = nethttp.redirect("POST", "/ü", 307)
    assert post.headers["location"] == "/%c3%bc"
    assert "content-type" not in post.headers


def test_error_like_http_error() -> None:
    response = nethttp.error("invalid range", 416)

    assert response.body == b"invalid range\n"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
