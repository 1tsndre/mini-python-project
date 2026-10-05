import datetime as dt

import pytest

from store_service.config import duration


@pytest.mark.parametrize(
    ("value", "nanos"),
    [
        ("0", 0),
        ("15m", 900_000_000_000),
        ("168h", 604_800_000_000_000),
        ("1h30m", 5_400_000_000_000),
        ("1.5h", 5_400_000_000_000),
        ("300ms", 300_000_000),
        ("-2s", -2_000_000_000),
        ("+5s", 5_000_000_000),
        ("1us", 1_000),
        ("1µs", 1_000),
        ("7ns", 7),
        (".5s", 500_000_000),
    ],
)
def test_parse(value: str, nanos: int) -> None:
    assert duration.parse_nanos(value) == nanos


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", 'time: invalid duration ""'),
        ("abc", 'time: invalid duration "abc"'),
        ("10", 'time: missing unit in duration "10"'),
        ("5x", 'time: unknown unit "x" in duration "5x"'),
        ("1..5s", 'time: missing unit in duration "1..5s"'),
        (".s", 'time: invalid duration ".s"'),
        ("٣s", 'time: invalid duration "\\xd9\\xa3s"'),
        ("5µx", 'time: unknown unit "\\xc2\\xb5x" in duration "5\\xc2\\xb5x"'),
        ("9223372036854775808ns", 'time: invalid duration "9223372036854775808ns"'),
    ],
)
def test_parse_errors_read_like_go(value: str, message: str) -> None:
    with pytest.raises(ValueError) as error:
        duration.parse(value)
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("nanos", "expected"),
    [
        (0, "0s"),
        (35_000_000_000, "35s"),
        (60_000_000_000, "1m0s"),
        (5_400_000_000_000, "1h30m0s"),
        (250_000_000, "250ms"),
        (1_500, "1.5µs"),
        (42, "42ns"),
        (1_234_567_890, "1.23456789s"),
        (-2_000_000_000, "-2s"),
    ],
)
def test_formats_like_go(nanos: int, expected: str) -> None:
    assert duration.format_nanos(nanos) == expected


def test_timedelta_round_trip() -> None:
    assert duration.format(duration.parse("1h30m")) == "1h30m0s"
    assert duration.parse("15m") == dt.timedelta(minutes=15)
