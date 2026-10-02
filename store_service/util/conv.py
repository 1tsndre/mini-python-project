_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def atoi(text: str) -> int:
    """Like strconv.Atoi with the error ignored: anything that is not an integer reads as 0, and an
    integer out of range is clamped to the nearest 64-bit limit.
    """
    negative = text[:1] == "-"
    body = text[1:] if text[:1] in ("+", "-") else text
    if not body or not all("0" <= ch <= "9" for ch in body):
        return 0
    digits = body.lstrip("0") or "0"
    # Too long for an int64 whatever the digits; this also spares int() a number it would refuse.
    if len(digits) > len(str(_INT64_MAX)):
        return _INT64_MIN if negative else _INT64_MAX
    number = -int(digits) if negative else int(digits)
    return max(_INT64_MIN, min(_INT64_MAX, number))
