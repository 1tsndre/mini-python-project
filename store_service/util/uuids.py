"""Go's github.com/google/uuid Parse.

Python's uuid.UUID is not used for input because it accepts forms Go rejects, e.g. surrounding
whitespace removed or "urn:uuid:" with any case mix of other prefixes. Like Go, the rules apply to
the UTF-8 bytes of the text.
"""

import uuid

_URN_PREFIX = b"urn:uuid:"
_BYTE_OFFSETS = (0, 2, 4, 6, 9, 11, 14, 16, 19, 21, 24, 26, 28, 30, 32, 34)
_HEX = b"0123456789abcdefABCDEF"


def parse(value: str) -> uuid.UUID | None:
    s = value.encode("utf-8", "surrogatepass")
    if len(s) == 36:
        start = 0
    elif len(s) == 36 + 9:
        if s[:9].lower() != _URN_PREFIX:
            return None
        start = 9
    elif len(s) == 36 + 2:
        # Like Go, the enclosing characters themselves are not checked.
        start = 1
    elif len(s) == 32:
        return _from_hex(s, 0, range(0, 32, 2))
    else:
        return None
    if not all(s[start + i] == ord("-") for i in (8, 13, 18, 23)):
        return None
    return _from_hex(s, start, _BYTE_OFFSETS)


def _from_hex(s: bytes, start: int, offsets) -> uuid.UUID | None:
    out = bytearray()
    for offset in offsets:
        pair = s[start + offset : start + offset + 2]
        if len(pair) != 2 or pair[0] not in _HEX or pair[1] not in _HEX:
            return None
        out.append(int(pair, 16))
    return uuid.UUID(bytes=bytes(out))
