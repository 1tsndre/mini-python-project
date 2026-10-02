import datetime as dt
import enum
import functools
import typing
import uuid
from dataclasses import fields
from typing import Any


@functools.cache
def _enum_fields(cls: type) -> dict[str, type[enum.Enum]]:
    """The fields of a model whose type is an enum, so their columns are read as that enum."""
    hints = typing.get_type_hints(cls)
    return {name: kind for name, kind in hints.items() if isinstance(kind, type) and issubclass(kind, enum.Enum)}


def _value(value: Any, kind: type[enum.Enum] | None) -> Any:
    if kind is not None:
        return kind(value)
    if isinstance(value, dt.datetime):
        # Timestamps in the server's time zone, the way the Go service reports them.
        return value.astimezone()
    if isinstance(value, uuid.UUID) and type(value) is not uuid.UUID:
        return uuid.UUID(bytes=value.bytes)
    return value


def scan[T](cls: type[T], row: Any) -> T:
    enums = _enum_fields(cls)
    return cls(**{key: _value(value, enums.get(key)) for key, value in row.items()})


def scan_into(target: Any, row: Any) -> None:
    """Writes the row's columns into an existing model, like sqlx's StructScan into a struct."""
    names = {f.name for f in fields(target)}
    enums = _enum_fields(type(target))
    for key, value in row.items():
        if key in names:
            setattr(target, key, _value(value, enums.get(key)))
