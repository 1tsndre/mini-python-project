import pytest

from store_service.util.conv import atoi
from store_service.util.strings import to_lower


# Like Go's strconv.Atoi with the error ignored.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", 0),
        ("7", 7),
        ("+7", 7),
        ("-7", -7),
        ("007", 7),
        ("abc", 0),
        ("1.5", 0),
        ("1_000", 0),
        ("+", 0),
        ("٣", 0),
        ("99999999999999999999", 2**63 - 1),
        ("-99999999999999999999", -(2**63)),
        ("9223372036854775807", 2**63 - 1),
        ("-9223372036854775808", -(2**63)),
        ("0" * 5000 + "7", 7),
        ("1" * 5000, 2**63 - 1),
    ],
    ids=lambda value: value[:24] if isinstance(value, str) else None,
)
def test_atoi(value: str, expected: int) -> None:
    assert atoi(value) == expected


def test_to_lower_maps_one_character_at_a_time_like_go() -> None:
    assert to_lower("Andreas@Example.COM") == "andreas@example.com"
    assert to_lower("İSTANBUL") == "istanbul"
