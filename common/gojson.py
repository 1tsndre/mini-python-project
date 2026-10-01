"""JSON as Go's encoding/json writes and reads it.

The Python service answers byte for byte like the Go service, so encoding follows Go's rules:
struct fields in declaration order, map keys sorted, <, > and & escaped, decimals as strings
without trailing zeros, timestamps in RFC 3339 with nanosecond precision and a trailing newline
after each response. Decoding follows json.Decoder: only the first value is read, field names
match case-insensitively, unknown fields are ignored and a value of the wrong type is an error.
"""

import codecs
import dataclasses
import datetime as dt
import decimal
import json
import re
import types
import typing
import uuid
from typing import Any

from common import decimals

_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", " ": "\\u2028", " ": "\\u2029"}
_ESCAPE_RE = re.compile("[<>&  ]")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_WHITESPACE = " \t\n\r"
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
_INT64_DIGITS = len(str(_INT64_MIN))

# Field metadata, like Go struct tags:
# OMIT_EMPTY is `json:",omitempty"` (drop None, "", 0, False and empty lists/dicts),
# OMIT_NIL is omitempty on a pointer or interface (drop None only) and SKIP is `json:"-"`.
OMIT_EMPTY = {"json": "omitempty"}
OMIT_NIL = {"json": "omitnil"}
SKIP = {"json": "-"}


class DecodeError(ValueError):
    """The body is not valid JSON, or a value has the wrong type for its field."""


def marshal(value: Any) -> bytes:
    """Like json.Marshal."""
    text = json.dumps(_plain(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group()], text).encode()


def encode(value: Any) -> bytes:
    """Like json.Encoder.Encode: the JSON followed by a newline."""
    return marshal(value) + b"\n"


def format_time(t: dt.datetime) -> str:
    """Go's time.RFC3339Nano: fractional seconds without trailing zeros and "Z" for UTC."""
    text = f"{t.year:04d}-{t.month:02d}-{t.day:02d}T{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    if t.microsecond:
        text += "." + f"{t.microsecond:06d}".rstrip("0")
    offset = t.utcoffset() or dt.timedelta()
    if offset == dt.timedelta():
        return text + "Z"
    minutes = int(offset.total_seconds()) // 60
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{text}{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        # Python strings can hold lone surrogates; Go writes them as U+FFFD.
        return _SURROGATE_RE.sub("�", value)
    if isinstance(value, decimal.Decimal):
        return decimals.format(value)
    if isinstance(value, dt.datetime):
        return format_time(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result = {}
        for f in dataclasses.fields(value):
            option = f.metadata.get("json")
            if option == "-":
                continue
            item = getattr(value, f.name)
            if option == "omitnil" and item is None:
                continue
            if option == "omitempty" and _is_empty(item):
                continue
            result[f.name] = _plain(item)
        return result
    if isinstance(value, dict):
        # Go writes map keys in sorted order.
        return {str(k): _plain(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def _is_empty(value: Any) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 0
    return isinstance(value, str | list | tuple | dict) and not value


class JSONObject(list):
    """A decoded JSON object as its (key, value) pairs, in order and with duplicates kept."""


def _go_replace(error: UnicodeDecodeError) -> tuple[str, int]:
    # Go's decoder replaces every invalid byte with U+FFFD; Python's "replace" may use one for several.
    return "�" * (error.end - error.start), error.end


codecs.register_error("go_replace", _go_replace)


def _reject_constant(name: str) -> Any:
    raise DecodeError(f"invalid character in JSON: {name}")


def _parse_int(text: str) -> int | float:
    # An integer too long for an int64 can only be ignored or rejected, as in Go, and Python is slow to
    # convert a huge one (and refuses beyond 4300 digits), so it is kept as a float.
    return int(text) if len(text) <= _INT64_DIGITS else float(text)


_DECODER = json.JSONDecoder(object_pairs_hook=JSONObject, parse_constant=_reject_constant, parse_int=_parse_int)


def decode(body: bytes) -> Any:
    """Reads the first JSON value of body, like json.Decoder.Decode; anything after it is ignored."""
    text = body.decode("utf-8", "go_replace")
    start = 0
    while start < len(text) and text[start] in _WHITESPACE:
        start += 1
    if start == len(text):
        raise DecodeError("EOF")
    try:
        value, _ = _DECODER.raw_decode(text, start)
    except (ValueError, RecursionError) as e:  # RecursionError: nested too deeply
        raise DecodeError(str(e)) from e
    return value


def bind[T](value: Any, cls: type[T]) -> T:
    """Fills the dataclass cls from a decoded JSON value, like json.Unmarshal into a struct.

    A JSON null gives the zero value. Fields are str, int (a 64-bit integer) or int | None (a
    pointer, where null means nil); a null for a str or int field leaves it unchanged.
    """
    instance = cls()
    if value is None:
        return instance
    if not isinstance(value, JSONObject):
        raise DecodeError(f"cannot unmarshal {type(value).__name__} into {cls.__name__}")

    hints = typing.get_type_hints(cls)
    names = [f.name for f in dataclasses.fields(cls) if f.metadata.get("json") != "-"]
    for key, item in value:
        name = _match_field(key, names)
        if name is None:
            continue
        kind = hints[name]
        if item is None:
            if _is_optional(kind):
                setattr(instance, name, None)
            continue
        setattr(instance, name, _convert(item, kind, name))
    return instance


def _match_field(key: str, names: list[str]) -> str | None:
    # An exact match wins; otherwise the name is matched case-insensitively.
    if key in names:
        return key
    folded = _fold(key)
    for name in names:
        if _fold(name) == folded:
            return name
    return None


def _fold(text: str) -> str:
    """Simple case folding, one character at a time, as Go compares field names."""
    out = []
    for ch in text:
        folded = ch.casefold()
        out.append(folded if len(folded) == 1 else ch.lower() if len(ch.lower()) == 1 else ch)
    return "".join(out)


def _is_optional(kind: Any) -> bool:
    return isinstance(kind, types.UnionType) and type(None) in typing.get_args(kind)


def _convert(item: Any, kind: Any, name: str) -> Any:
    if _is_optional(kind):
        kind = next(arg for arg in typing.get_args(kind) if arg is not type(None))
    if kind is str:
        if not isinstance(item, str):
            raise DecodeError(f"field {name}: expected a string")
        return _SURROGATE_RE.sub("�", item)
    if kind is int:
        if not isinstance(item, int) or isinstance(item, bool) or not _INT64_MIN <= item <= _INT64_MAX:
            raise DecodeError(f"field {name}: expected a 64-bit integer")
        return item
    raise TypeError(f"unsupported field type {kind!r} for {name}")


_TIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})")


def parse_time(text: str) -> dt.datetime:
    """Parses an RFC 3339 timestamp as Go's time.Time does, keeping its offset; extra precision
    beyond microseconds is dropped.
    """
    m = _TIME_RE.fullmatch(text)
    if m is None:
        raise DecodeError(f"cannot parse {text!r} as RFC 3339")
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    micros = int((m.group(7) or "0").ljust(9, "0")[:6])
    zone = m.group(8)
    if zone == "Z":
        tz = dt.UTC
    else:
        sign = 1 if zone[0] == "+" else -1
        tz = dt.timezone(sign * dt.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
    return dt.datetime(year, month, day, hour, minute, second, micros, tzinfo=tz)


def unmarshal[T](data: bytes, cls: type[T]) -> T:
    """Decodes a whole JSON document into the dataclass cls, like json.Unmarshal: anything after the
    value is an error, and null gives the zero value.
    """
    try:
        value = _DECODER.decode(data.decode("utf-8", "go_replace"))
    except (ValueError, RecursionError) as e:  # RecursionError: nested too deeply
        raise DecodeError(str(e)) from e
    if value is None and dataclasses.is_dataclass(cls):
        return cls()
    return _unmarshal_value(value, cls, cls.__name__)


def _unmarshal_value(value: Any, kind: Any, name: str) -> Any:
    if _is_optional(kind):
        if value is None:
            return None
        kind = next(arg for arg in typing.get_args(kind) if arg is not type(None))
    origin = typing.get_origin(kind)
    if origin is list:
        if not isinstance(value, list) or isinstance(value, JSONObject):
            raise DecodeError(f"field {name}: expected an array")
        (item_kind,) = typing.get_args(kind)
        return [_unmarshal_value(item, item_kind, name) for item in value]
    if dataclasses.is_dataclass(kind):
        if not isinstance(value, JSONObject):
            raise DecodeError(f"field {name}: expected an object")
        instance = kind()
        hints = typing.get_type_hints(kind)
        names = [f.name for f in dataclasses.fields(kind) if f.metadata.get("json") != "-"]
        for key, item in value:
            field_name = _match_field(key, names)
            if field_name is None or item is None and not _is_optional(hints[field_name]):
                continue
            setattr(instance, field_name, _unmarshal_value(item, hints[field_name], field_name))
        return instance
    if kind is uuid.UUID:
        if not isinstance(value, str):
            raise DecodeError(f"field {name}: expected a UUID string")
        return uuid.UUID(value)
    if kind is decimal.Decimal:
        # Like decimal.UnmarshalJSON, a quoted or an unquoted number.
        parsed = decimals.parse(value if isinstance(value, str) else str(value))
        if parsed is None:
            raise DecodeError(f"field {name}: expected a decimal")
        return parsed
    if kind is dt.datetime:
        if not isinstance(value, str):
            raise DecodeError(f"field {name}: expected a timestamp string")
        return parse_time(value)
    return _convert(value, kind, name)
