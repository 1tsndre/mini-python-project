import asyncpg
import pytest

from store_service.repository.errors import DuplicateKeyError, ForeignKeyViolationError, translate_error

UNIQUE = asyncpg.UniqueViolationError.new({"C": "23505", "M": "duplicate key", "n": "users_email_key"})
FOREIGN_KEY = asyncpg.ForeignKeyViolationError.new({"C": "23503", "M": "foreign key violation"})
CHECK = asyncpg.CheckViolationError.new({"C": "23514", "M": "check violation"})
PLAIN = ConnectionResetError("connection reset")


@pytest.mark.parametrize(
    ("error", "expected"),
    [(UNIQUE, DuplicateKeyError), (FOREIGN_KEY, ForeignKeyViolationError)],
    ids=["unique violation", "foreign key violation"],
)
def test_constraint_violations_are_translated(error: Exception, expected: type[Exception]) -> None:
    assert isinstance(translate_error(error), expected)


@pytest.mark.parametrize("error", [CHECK, PLAIN], ids=["other constraint violation", "other error"])
def test_other_errors_are_passed_through(error: Exception) -> None:
    assert translate_error(error) is error


def test_constraint_violations_keep_the_driver_error_for_logging() -> None:
    cause = translate_error(UNIQUE).__cause__

    assert isinstance(cause, asyncpg.UniqueViolationError)
    assert cause.constraint_name == "users_email_key"
