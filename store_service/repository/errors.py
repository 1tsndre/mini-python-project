"""Errors the repositories raise for expected database outcomes, so services can tell them apart
without depending on the driver.
"""

import asyncpg


class RecordNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__("record not found")


class DuplicateKeyError(Exception):
    def __init__(self, cause: Exception) -> None:
        super().__init__(f"duplicate key: {cause}")


class ForeignKeyViolationError(Exception):
    """A write referenced a row that does not exist, or a delete removed a row still referenced."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"foreign key violation: {cause}")


def translate_error(error: Exception) -> Exception:
    """Maps driver errors to the errors above. Constraint violations keep the driver error as the
    cause, so it still shows up in logs.
    """
    if isinstance(error, asyncpg.UniqueViolationError):
        translated: Exception = DuplicateKeyError(error)
    elif isinstance(error, asyncpg.ForeignKeyViolationError):
        translated = ForeignKeyViolationError(error)
    else:
        return error
    translated.__cause__ = error
    return translated
