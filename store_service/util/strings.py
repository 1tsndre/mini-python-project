def to_lower(text: str) -> str:
    """Like Go's strings.ToLower: one character at a time, without Python's special cases
    (e.g. "İ" becomes "i", not "i̇").
    """
    out = []
    for ch in text:
        lowered = ch.lower()
        out.append(lowered if len(lowered) == 1 else _SIMPLE_LOWER.get(ch, ch))
    return "".join(out)


# Characters whose full lower-case mapping has several characters, with their simple mapping.
_SIMPLE_LOWER = {"İ": "i"}
