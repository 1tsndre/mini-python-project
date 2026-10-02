"""Go's time.ParseDuration and Duration.String, so the same .env values work in every service."""

import datetime as dt

_NANOS = {
    "ns": 1,
    "us": 1_000,
    "µs": 1_000,  # U+00B5 micro sign
    "μs": 1_000,  # U+03BC Greek small letter mu
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60 * 1_000_000_000,
    "h": 3600 * 1_000_000_000,
}
_TWO_POW_63 = 1 << 63


def _quote(text: str) -> str:
    out = ['"']
    for ch in text:
        if ord(ch) >= 0x80 or ch < " ":
            out.extend(f"\\x{b:02x}" for b in ch.encode())
        else:
            if ch in '"\\':
                out.append("\\")
            out.append(ch)
    out.append('"')
    return "".join(out)


def _invalid(orig: str) -> ValueError:
    return ValueError(f"time: invalid duration {_quote(orig)}")


def parse_nanos(value: str) -> int:
    """Parses "300ms", "-1.5h" or "2h45m" into nanoseconds, step by step as Go does."""
    orig, s, d, negative = value, value, 0, False
    if s and s[0] in "-+":
        negative = s[0] == "-"
        s = s[1:]
    if s == "0":
        return 0
    if not s:
        raise _invalid(orig)

    while s:
        if not (s[0] == "." or "0" <= s[0] <= "9"):
            raise _invalid(orig)

        i, v = 0, 0
        while i < len(s) and "0" <= s[i] <= "9":
            if v > _TWO_POW_63 // 10:
                raise _invalid(orig)
            v = v * 10 + ord(s[i]) - ord("0")
            if v > _TWO_POW_63:
                raise _invalid(orig)
            i += 1
        pre = i > 0
        s = s[i:]

        f, scale, post = 0, 1.0, False
        if s and s[0] == ".":
            s = s[1:]
            j, overflow = 0, False
            while j < len(s) and "0" <= s[j] <= "9":
                if not overflow:
                    if f > (_TWO_POW_63 - 1) // 10:
                        overflow = True
                    else:
                        y = f * 10 + ord(s[j]) - ord("0")
                        if y > _TWO_POW_63:
                            overflow = True
                        else:
                            f = y
                            scale *= 10
                j += 1
            post = j > 0
            s = s[j:]
        if not pre and not post:
            raise _invalid(orig)

        u = 0
        while u < len(s) and s[u] != "." and not "0" <= s[u] <= "9":
            u += 1
        if u == 0:
            raise ValueError(f"time: missing unit in duration {_quote(orig)}")
        name, s = s[:u], s[u:]
        unit = _NANOS.get(name)
        if unit is None:
            raise ValueError(f"time: unknown unit {_quote(name)} in duration {_quote(orig)}")
        if v > _TWO_POW_63 // unit:
            raise _invalid(orig)
        v *= unit
        if f > 0:
            # Go uses float64 here to be nanosecond accurate for fractions of hours.
            v += int(float(f) * (float(unit) / scale))
            if v > _TWO_POW_63:
                raise _invalid(orig)
        d += v
        if d > _TWO_POW_63:
            raise _invalid(orig)

    if negative:
        return -d
    if d > _TWO_POW_63 - 1:
        raise _invalid(orig)
    return d


def parse(value: str) -> dt.timedelta:
    return dt.timedelta(microseconds=parse_nanos(value) / 1000)


def format_nanos(nanos: int) -> str:
    """Prints like Go's Duration.String: "35s", "1m0s", "1h30m0s", "250ms"."""
    if nanos == 0:
        return "0s"
    sign = "-" if nanos < 0 else ""
    u = abs(nanos)
    if u < 1_000_000_000:
        if u < 1_000:
            return f"{sign}{u}ns"
        if u < 1_000_000:
            return f"{sign}{_fraction(u, 1_000)}µs"
        return f"{sign}{_fraction(u, 1_000_000)}ms"
    hours, rest = divmod(u, 3600 * 1_000_000_000)
    minutes, rest = divmod(rest, 60 * 1_000_000_000)
    seconds = _fraction(rest, 1_000_000_000) + "s"
    if hours:
        return f"{sign}{hours}h{minutes}m{seconds}"
    if minutes:
        return f"{sign}{minutes}m{seconds}"
    return f"{sign}{seconds}"


def format(value: dt.timedelta) -> str:
    return format_nanos(round(value.total_seconds() * 1_000_000) * 1000)


def _fraction(value: int, unit: int) -> str:
    whole, rest = divmod(value, unit)
    if rest == 0:
        return str(whole)
    digits = f"{rest:0{len(str(unit)) - 1}d}".rstrip("0")
    return f"{whole}.{digits}"
