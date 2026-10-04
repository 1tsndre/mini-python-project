import decimal
from typing import Any

import pytest

from store_service.model import ProductFilter
from store_service.repository.product_repository import product_filter_query, product_order_by


@pytest.mark.parametrize(
    ("product_filter", "where", "args"),
    [
        (ProductFilter(page=1, per_page=10), "", []),
        (
            ProductFilter(category_id="cat", store_id="store", search="mug", min_price="10", max_price="20.5"),
            " WHERE category_id = $1 AND store_id = $2 AND (name ILIKE $3 OR description ILIKE $3)"
            " AND price >= $4 AND price <= $5",
            ["cat", "store", "%mug%", decimal.Decimal("10"), decimal.Decimal("20.5")],
        ),
        (ProductFilter(min_price="cheap", max_price="20"), " WHERE price <= $1", [decimal.Decimal("20")]),
    ],
    ids=["no filters", "all filters, with the search placeholder used twice", "unparsable prices are ignored"],
)
def test_product_filter_query(product_filter: ProductFilter, where: str, args: list[Any]) -> None:
    assert product_filter_query(product_filter) == (where, args)


@pytest.mark.parametrize(
    ("sort_by", "sort_order", "want"),
    [
        ("", "", " ORDER BY created_at DESC, id DESC"),
        ("price", "asc", " ORDER BY price ASC, id ASC"),
        ("name", "desc", " ORDER BY name DESC, id DESC"),
        # Anything outside the whitelist falls back to the default column.
        ("price; DROP TABLE products", "asc", " ORDER BY created_at ASC, id ASC"),
    ],
)
def test_product_order_by(sort_by: str, sort_order: str, want: str) -> None:
    assert product_order_by(ProductFilter(sort_by=sort_by, sort_order=sort_order)) == want
