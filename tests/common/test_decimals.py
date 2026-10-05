import decimal

import pytest

from common import decimals


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15000", "15000"),
        ("15000.50", "15000.50"),
        ("1e3", "1000"),
        ("1E-2", "0.01"),
        (".5", "0.5"),
        ("-.5", "-0.5"),
        (".-5", "-0.05"),
        ("+7", "7"),
        ("1.", "1"),
        ("12345678901234567890.5", "12345678901234567890.5"),
        ("1" * 5000, "1" * 5000),
    ],
    ids=lambda value: value[:24],
)
def test_parses_like_shopspring(text: str, expected: str) -> None:
    assert decimals.parse(text) == decimal.Decimal(expected)


@pytest.mark.parametrize(
    "text",
    ["", ".", "-", "abc", "1.2.3", "1e", "1e1.5", " 1", "1_000", "0x10", "NaN", "Infinity", "١٢", "1e2147483648"],
)
def test_rejects(text: str) -> None:
    assert decimals.parse(text) is None


def test_there_is_no_negative_zero() -> None:
    assert not decimals.parse("-0.00").is_signed()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("15000.00", "15000"),
        ("15000.50", "15000.5"),
        ("0.00", "0"),
        ("-1.10", "-1.1"),
        ("1E+3", "1000"),
        ("0.010", "0.01"),
    ],
)
def test_formats_without_trailing_zeros(value: str, expected: str) -> None:
    assert decimals.format(decimal.Decimal(value)) == expected
