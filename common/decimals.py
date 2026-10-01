"""shopspring/decimal, which the Go service uses for prices: NewFromString and String.

Parsing accepts forms such as "1e3", ".5" and "-.5", and rejects what Python's Decimal also
takes, like "NaN", "Infinity", surrounding whitespace or underscores.
"""

import decimal

_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1


def parse(value: str) -> decimal.Decimal | None:
    s, exp = value, 0
    e = next((i for i, ch in enumerate(s) if ch in "eE"), -1)
    if e >= 0:
        exponent = _parse_int32(s[e + 1 :])
        if exponent is None:
            return None
        s, exp = s[:e], exponent

    if s.count(".") > 1:
        return None
    point = s.find(".")
    digits = s
    if point >= 0:
        digits = s[:point] + s[point + 1 :]
        exp -= len(s) - point - 1

    integer = _split_integer(digits)
    if integer is None or not _INT32_MIN <= exp <= _INT32_MAX:
        return None
    negative, body = integer
    # Built from the digits, never through int(), which refuses numbers of more than 4300 digits.
    # Like math/big, there is no negative zero.
    sign = 1 if negative and body.strip("0") else 0
    return decimal.Decimal((sign, tuple(int(d) for d in body), exp))


def _split_integer(text: str) -> tuple[bool, str] | None:
    """An optional sign followed by ASCII digits, as Go's strconv and math/big accept: whether it is
    negative, and the digits.
    """
    body = text[1:] if text[:1] in ("+", "-") else text
    if not body or not all("0" <= ch <= "9" for ch in body):
        return None
    return text[:1] == "-", body


def _parse_int32(text: str) -> int | None:
    """Like strconv.ParseInt(text, 10, 32), as an exponent is read."""
    integer = _split_integer(text)
    if integer is None:
        return None
    negative, body = integer
    digits = body.lstrip("0") or "0"
    if len(digits) > len(str(_INT32_MAX)):
        return None
    number = -int(digits) if negative else int(digits)
    return number if _INT32_MIN <= number <= _INT32_MAX else None


def format(value: decimal.Decimal) -> str:
    """Like decimal.Decimal.String: plain notation without trailing fractional zeros."""
    if value.is_zero():
        return "0"
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
