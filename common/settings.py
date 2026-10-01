"""Settings lookup like viper with AutomaticEnv and a .env config file, as the Go services read
their configuration: a non-empty environment variable wins, then the .env file (whose keys match
case-insensitively, and where an empty value still counts), then the default.
"""

import os
import re

from dotenv import dotenv_values

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


class Settings:
    def __init__(self, env_file: str = ".env") -> None:
        values = dotenv_values(env_file) if os.path.exists(env_file) else {}
        self._file = {key.lower(): value or "" for key, value in values.items()}

    def lookup(self, key: str) -> str | None:
        """The configured value of key, or None when neither the environment nor .env sets it."""
        value = os.environ.get(key)
        if value:
            return value
        return self._file.get(key.lower())

    def string(self, key: str, default: str) -> str:
        value = self.lookup(key)
        return default if value is None else value

    def integer(self, key: str, default: int) -> int:
        value = self.lookup(key)
        return default if value is None else to_int(value)


def to_int(value: str) -> int:
    """Like viper's GetInt (spf13/cast): Go's base-0 integer syntax, and 0 for anything else."""
    text = value
    if re.fullmatch(r"-?[0-9]+\.0+", text):
        text = text[: text.index(".")]
    if not text.isascii() or not re.fullmatch(r"[+-]?[0-9a-zA-Z_]+", text):
        return 0
    try:
        number = int(text, 0)
    except ValueError:
        if not re.fullmatch(r"[+-]?0[0-7]+", text):
            return 0
        number = int(text, 8)  # Go reads a leading 0 as octal
    return number if _INT64_MIN <= number <= _INT64_MAX else 0
