import pytest

from store_service import pagination


@pytest.mark.parametrize(
    ("page", "per_page", "want"),
    [
        (0, 0, (1, 10)),
        (-5, -1, (1, 10)),
        (3, 25, (3, 25)),
        (2, 101, (2, 100)),
        (2**63 - 1, 99_999_999_999, (2**63 - 1, 100)),
    ],
)
def test_normalize(page: int, per_page: int, want: tuple[int, int]) -> None:
    assert pagination.normalize(page, per_page) == want


def test_total_pages() -> None:
    assert pagination.total_pages(0, 10) == 0
    assert pagination.total_pages(10, 10) == 1
    assert pagination.total_pages(11, 10) == 2


def test_offset_wraps_like_go_int64() -> None:
    assert pagination.offset(3, 10) == 20
    # Go computes (page-1)*perPage in int64; PostgreSQL then rejects the negative offset.
    assert pagination.offset(2**63 - 1, 100) == -200
