DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 10
MAX_PER_PAGE = 100


def normalize(page: int, per_page: int) -> tuple[int, int]:
    if page <= 0:
        page = DEFAULT_PAGE
    if per_page <= 0:
        per_page = DEFAULT_PER_PAGE
    if per_page > MAX_PER_PAGE:
        per_page = MAX_PER_PAGE
    return page, per_page


def total_pages(total: int, per_page: int) -> int:
    pages = total // per_page
    if total % per_page != 0:
        pages += 1
    return pages


def offset(page: int, per_page: int) -> int:
    """(page - 1) * per_page with Go's int64 wrap-around."""
    value = ((page - 1) * per_page) & 0xFFFF_FFFF_FFFF_FFFF
    return value - (1 << 64) if value >= 1 << 63 else value
